import json
import os
from pathlib import Path
from typing import Literal, Optional, TypedDict

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "transcripts"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


class TranscriptState(TypedDict):
    symbol: str
    quarter: int
    year: int
    transcript: Optional[dict]
    cache_hit: bool
    error: Optional[str]


def _transcript_path(symbol: str, quarter: int, year: int) -> Path:
    return DATA_DIR / symbol.upper() / f"Q{quarter}_{year}.json"


def check_cache(state: TranscriptState) -> TranscriptState:
    path = _transcript_path(state["symbol"], state["quarter"], state["year"])
    if path.exists():
        with open(path) as f:
            return {**state, "transcript": json.load(f), "cache_hit": True}
    return {**state, "cache_hit": False}


def should_generate(state: TranscriptState) -> Literal["generate", END]:
    return END if state["cache_hit"] else "generate"


def generate_transcript(state: TranscriptState) -> TranscriptState:
    llm = ChatGoogleGenerativeAI(model=GEMINI_MODEL, temperature=0.7)

    symbol = state["symbol"]
    quarter = state["quarter"]
    year = state["year"]

    # Map quarter to approximate month for realistic date generation
    quarter_months = {1: "02", 2: "05", 3: "08", 4: "11"}
    month = quarter_months[quarter]

    prompt = f"""Generate a realistic synthetic earnings call transcript for {symbol} for Q{quarter} {year}.

Return ONLY a valid JSON object with these exact fields (no markdown, no code fences):
{{
    "symbol": "{symbol}",
    "quarter": {quarter},
    "year": {year},
    "date": "{year}-{month}-01 17:00:00",
    "content": "<full transcript text>"
}}

Requirements for the content field:
- Include an operator introduction
- CEO opening remarks with specific revenue, EPS, and growth metrics
- CFO financial details section with YoY comparisons, margins, and cash flow
- Forward guidance for next quarter
- Q&A section with 3-4 analyst questions and executive responses
- Minimum 1200 words
- All financial figures must be internally consistent
- Use realistic numbers appropriate for {symbol}'s actual industry and scale

Return only valid JSON."""

    response = llm.invoke([HumanMessage(content=prompt)])
    raw = response.content.strip()

    # Strip markdown code fences if Gemini wraps the response
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        transcript = json.loads(raw)
        return {**state, "transcript": transcript, "error": None}
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                transcript = json.loads(raw[start:end])
                return {**state, "transcript": transcript, "error": None}
            except json.JSONDecodeError:
                pass
        return {**state, "error": f"Failed to parse Gemini response: {raw[:300]}"}


def save_transcript(state: TranscriptState) -> TranscriptState:
    if state.get("error") or not state.get("transcript"):
        return state
    path = _transcript_path(state["symbol"], state["quarter"], state["year"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state["transcript"], f, indent=2)
    return state


_graph = StateGraph(TranscriptState)
_graph.add_node("check_cache", check_cache)
_graph.add_node("generate", generate_transcript)
_graph.add_node("save", save_transcript)

_graph.add_edge(START, "check_cache")
_graph.add_conditional_edges("check_cache", should_generate, {"generate": "generate", END: END})
_graph.add_edge("generate", "save")
_graph.add_edge("save", END)

transcript_graph = _graph.compile()


def get_transcript(symbol: str, quarter: int, year: int) -> dict:
    """Fetch or generate a transcript. Returns the transcript dict."""
    result = transcript_graph.invoke({
        "symbol": symbol.upper(),
        "quarter": quarter,
        "year": year,
        "transcript": None,
        "cache_hit": False,
        "error": None,
    })
    if result.get("error"):
        raise ValueError(result["error"])
    return result["transcript"]
