"""FastAPI router — graph queries and prediction endpoints."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from src.agents.prediction_agent import run_prediction
from src.db.connection import get_db
from src.db.graph_queries import fetch_company_graph
from src.db.graph_queries import fetch_analyst_targets, fetch_guidance, fetch_key_metrics_history, fetch_segments
from src.db.graph_queries import hop1_sentiment, hop2_competitor_signals
from src.agents.signal_agent import compute_signals
from src.models.graph_models import CompanyGraph, IngestResult, PredictionReport, SignalBundle

router = APIRouter(prefix="/v1", tags=["graph"])

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "transcripts"


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class PredictionRequest(BaseModel):
    quarter: int
    year: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/graph/ingest/{symbol}", response_model=IngestResult)
async def ingest_symbol(symbol: str, background_tasks: BackgroundTasks):
    """Trigger extraction agent for all unprocessed local transcripts of a symbol.

    Extraction runs in a FastAPI background task — returns immediately with counts.
    """
    from src.agents.extraction_agent import run_extraction

    sym = symbol.upper()
    sym_dir = DATA_DIR / sym
    if not sym_dir.exists():
        raise HTTPException(status_code=404, detail=f"No transcripts found for {sym}")

    files = list(sym_dir.glob("*.json"))
    ingested = skipped = errors = 0

    for f in files:
        parts = f.stem.split("_")
        if len(parts) != 2:
            continue
        try:
            q, y = int(parts[0].lstrip("Q")), int(parts[1])
        except ValueError:
            continue

        async def _extract(sym=sym, q=q, y=y):
            nonlocal ingested, errors
            try:
                result = await run_extraction(sym, q, y)
                if result.get("error"):
                    errors += 1
                else:
                    ingested += 1
            except Exception:
                errors += 1

        await _extract()

    return IngestResult(symbol=sym, ingested=ingested, skipped=skipped, errors=errors)


@router.get("/graph/company/{ticker}", response_model=dict)
async def get_company_graph(ticker: str):
    """Return company node with first-degree edges (competitors, suppliers, customers)."""
    db = await get_db()
    result = await fetch_company_graph(db, ticker)
    if not result:
        raise HTTPException(status_code=404, detail=f"Company {ticker.upper()} not found in graph.")
    return result


@router.get("/graph/signals/{ticker}", response_model=SignalBundle)
async def get_signals(ticker: str, quarter: int = 1, year: int = 2024):
    """Compute the 7-signal bundle without generating a narrative report."""
    db = await get_db()
    # Sequential queries — surrealdb async_ws client has a race condition with
    # concurrent queries sharing one connection (KeyError on qry dict).
    hop1     = await hop1_sentiment(db, ticker)
    hop2     = await hop2_competitor_signals(db, ticker)
    km       = await fetch_key_metrics_history(db, ticker)
    segs     = await fetch_segments(db, ticker)
    at       = await fetch_analyst_targets(db, ticker)
    guidance = await fetch_guidance(db, ticker, quarter, year)
    return compute_signals(
        symbol=ticker.upper(),
        quarter=quarter,
        year=year,
        hop1=hop1,
        hop2=hop2,
        km_history=km,
        segments=segs,
        analyst_targets=at,
        guidance=guidance,
    )


@router.post("/predictions/{ticker}", response_model=PredictionReport)
async def get_prediction(ticker: str, req: PredictionRequest):
    """Run the full prediction orchestrator and return a narrative Probability Report."""
    try:
        return await run_prediction(ticker, req.quarter, req.year)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
