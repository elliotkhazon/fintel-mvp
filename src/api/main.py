import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from src.models.transcript import Transcript, TranscriptDateEntry

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "transcripts"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.db.connection import get_db, close_db
    from src.db.init_schema import init_schema
    try:
        await get_db()
        await init_schema()
    except Exception as exc:
        print(f"[WARN] SurrealDB unavailable at startup: {exc}. Graph endpoints will fail until DB is running.")
    yield
    await close_db()


app = FastAPI(
    title="FMP Mock API",
    description="Synthetic FMP-compatible REST API powered by Gemini + LangGraph",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Graph + prediction router
from src.api.graph import router as graph_router
app.include_router(graph_router)


def _load_all_transcripts() -> List[dict]:
    transcripts = []
    if DATA_DIR.exists():
        for sym_dir in DATA_DIR.iterdir():
            if sym_dir.is_dir():
                for f in sym_dir.glob("*.json"):
                    with open(f) as fh:
                        transcripts.append(json.load(fh))
    return transcripts


def _load_symbol_transcripts(symbol: str) -> List[dict]:
    sym_dir = DATA_DIR / symbol.upper()
    if not sym_dir.exists():
        return []
    results = []
    for f in sym_dir.glob("*.json"):
        with open(f) as fh:
            results.append(json.load(fh))
    return results


# ---------------------------------------------------------------------------
# Endpoints matching FMP stable/v3/v4 signatures
# ---------------------------------------------------------------------------

@app.get(
    "/stable/search-transcripts",
    response_model=List[Transcript],
    summary="Search earnings call transcripts by keyword",
)
def search_transcripts(
    query: str = Query(..., description="Full-text search query"),
    limit: int = Query(10, ge=1, le=100, description="Results per page"),
    page: int = Query(0, ge=0, description="Zero-based page index"),
):
    all_transcripts = _load_all_transcripts()
    q = query.lower()
    matches = [
        t for t in all_transcripts
        if q in t.get("content", "").lower() or q in t.get("symbol", "").lower()
    ]
    start = page * limit
    return matches[start : start + limit]


@app.get(
    "/v3/earning_call_transcript/{symbol}",
    response_model=List[Transcript],
    summary="Get earning call transcript(s) for a symbol",
)
def get_earning_call_transcript(
    symbol: str,
    quarter: Optional[int] = Query(None, ge=1, le=4, description="Fiscal quarter (1-4)"),
    year: Optional[int] = Query(None, description="Fiscal year"),
):
    transcripts = _load_symbol_transcripts(symbol)
    if quarter is not None:
        transcripts = [t for t in transcripts if t.get("quarter") == quarter]
    if year is not None:
        transcripts = [t for t in transcripts if t.get("year") == year]
    return sorted(transcripts, key=lambda t: (t.get("year", 0), t.get("quarter", 0)), reverse=True)


@app.get(
    "/v4/transcript-dates",
    summary="Get available transcript dates for a symbol",
)
def get_transcript_dates(
    symbol: str = Query(..., description="Stock ticker symbol"),
):
    """Returns list of [symbol, quarter, year, date] arrays — matches FMP v4 format."""
    transcripts = _load_symbol_transcripts(symbol)
    dates = [
        [t["symbol"], t["quarter"], t["year"], t.get("date", "")[:10]]
        for t in transcripts
    ]
    return sorted(dates, key=lambda x: (x[2], x[1]), reverse=True)


@app.get("/health", summary="Health check")
def health():
    transcript_count = sum(
        1
        for sym_dir in DATA_DIR.iterdir()
        if sym_dir.is_dir()
        for _ in sym_dir.glob("*.json")
    ) if DATA_DIR.exists() else 0
    return {"status": "ok", "transcripts_loaded": transcript_count}


# ---------------------------------------------------------------------------
# FMP mock endpoints — key-metrics, segmentation, price-target-consensus
# ---------------------------------------------------------------------------
# These return static fixtures that mirror the real FMP response shape.
# Replace fixture data with real FMP calls when an API key is available.

_KEY_METRICS_FIXTURE = {
    "NVDA": [
        {"date": "2024-01-28", "period": "Q1", "symbol": "NVDA", "revenuePerShare": 9.73,
         "grossProfitMargin": 0.756, "inventoryTurnover": 4.2, "daysOfSalesOutstanding": 42.1},
        {"date": "2023-10-29", "period": "Q4", "symbol": "NVDA", "revenuePerShare": 6.01,
         "grossProfitMargin": 0.702, "inventoryTurnover": 3.8, "daysOfSalesOutstanding": 47.3},
        {"date": "2023-07-26", "period": "Q3", "symbol": "NVDA", "revenuePerShare": 3.62,
         "grossProfitMargin": 0.687, "inventoryTurnover": 3.5, "daysOfSalesOutstanding": 51.0},
    ],
    "GOOG": [
        {"date": "2024-01-30", "period": "Q1", "symbol": "GOOG", "revenuePerShare": 10.55,
         "grossProfitMargin": 0.558, "inventoryTurnover": None, "daysOfSalesOutstanding": 54.8},
        {"date": "2023-10-24", "period": "Q4", "symbol": "GOOG", "revenuePerShare": 9.92,
         "grossProfitMargin": 0.543, "inventoryTurnover": None, "daysOfSalesOutstanding": 56.2},
    ],
}

_SEGMENT_FIXTURE = {
    "NVDA": {"Data Center": 18_400_000_000, "Gaming": 2_647_000_000,
              "Professional Visualization": 427_000_000, "Automotive": 281_000_000},
    "GOOG": {"Google Services": 76_400_000_000, "Google Cloud": 9_192_000_000,
              "Other Bets": 495_000_000},
}

_PRICE_TARGET_FIXTURE = {
    "NVDA": [{"symbol": "NVDA", "targetHigh": 1100.0, "targetLow": 500.0,
               "targetConsensus": 875.0, "targetMedian": 880.0}],
    "GOOG": [{"symbol": "GOOG", "targetHigh": 200.0, "targetLow": 140.0,
               "targetConsensus": 175.0, "targetMedian": 175.0}],
}


@app.get("/api/v3/key-metrics/{symbol}", summary="FMP key metrics (mock)")
def get_key_metrics(
    symbol: str,
    period: Optional[str] = Query("quarter", description="annual | quarter"),
):
    sym = symbol.upper()
    data = _KEY_METRICS_FIXTURE.get(sym, [
        {"date": "2024-01-01", "period": "Q1", "symbol": sym,
         "revenuePerShare": 5.0, "grossProfitMargin": 0.50,
         "inventoryTurnover": 4.0, "daysOfSalesOutstanding": 45.0},
    ])
    return data


@app.get("/api/v3/revenue-product-segmentation/{symbol}", summary="FMP revenue segmentation (mock)")
def get_revenue_segmentation(symbol: str):
    sym = symbol.upper()
    return _SEGMENT_FIXTURE.get(sym, {"Primary Segment": 1_000_000_000})


@app.get("/api/v3/price-target-consensus/{symbol}", summary="FMP price target consensus (mock)")
def get_price_target_consensus(symbol: str):
    sym = symbol.upper()
    return _PRICE_TARGET_FIXTURE.get(sym, [
        {"symbol": sym, "targetHigh": 200.0, "targetLow": 100.0,
         "targetConsensus": 150.0, "targetMedian": 150.0}
    ])
