from pydantic import BaseModel
from typing import Optional


class Transcript(BaseModel):
    symbol: str
    quarter: int
    year: int
    date: str
    content: str


class TranscriptDateEntry(BaseModel):
    symbol: str
    quarter: int
    year: int
    date: str
