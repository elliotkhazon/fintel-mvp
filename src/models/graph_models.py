from pydantic import BaseModel


class SignalScore(BaseModel):
    name: str
    score: float          # normalized to [-1.0, 1.0]
    direction: str        # "bullish" | "bearish" | "neutral"
    evidence: str


class SignalBundle(BaseModel):
    symbol: str
    quarter: int
    year: int
    composite_score: float
    signals: list[SignalScore]
    beat_probability: str  # "Low" | "Medium" | "High"


class PredictionReport(BaseModel):
    symbol: str
    quarter: int
    year: int
    signals: SignalBundle
    report: str


class IngestResult(BaseModel):
    symbol: str
    ingested: int
    skipped: int
    errors: int


class CompanyGraph(BaseModel):
    ticker: str
    name: str
    sector: str | None
    industry: str | None
    metrics: list[dict]
    competitors: list[str]
    suppliers: list[str]
    customers: list[str]
