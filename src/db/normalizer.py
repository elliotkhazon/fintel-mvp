"""Idempotent upsert helpers for all SurrealDB node types — surrealdb v2.0.0.

SurrealDB v2.0.0 CBOR encoding requires `RecordID` objects (not strings) for
any field typed `record<T>` in a SCHEMAFULL table.  All helpers parse
"table:id" strings into RecordID via `_rid()` before writing.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from surrealdb import Datetime as SurrealDatetime
from surrealdb import RecordID
from surrealdb.connections.async_ws import AsyncWsSurrealConnection


def _slug(text: str) -> str:
    """Convert text to a safe SurrealDB record ID component."""
    return re.sub(r"[^a-z0-9]", "_", text.lower()).strip("_") or "unknown"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt(iso_str: str) -> SurrealDatetime:
    """Wrap an ISO-8601 string as a SurrealDB Datetime (CBOR tag 0)."""
    return SurrealDatetime(iso_str)


def _now_dt() -> SurrealDatetime:
    return SurrealDatetime(datetime.now(timezone.utc).isoformat())


def _ticker_slug(company_id: str) -> str:
    """Extract the slug part from 'company:nvda' → 'nvda'."""
    return company_id.split(":")[-1]


def _rid(id_str: str) -> RecordID:
    """Parse 'table:identifier' → RecordID object required by surrealdb v2."""
    table, identifier = id_str.split(":", 1)
    return RecordID(table_name=table, identifier=identifier)


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------

async def upsert_company(
    db: AsyncWsSurrealConnection,
    ticker: str,
    name: str,
    sector: str | None = None,
    industry: str | None = None,
) -> str:
    record_id = f"company:{_slug(ticker)}"
    await db.upsert(record_id, {
        "ticker": ticker.upper(),
        "name": name,
        "sector": sector,
        "industry": industry,
    })
    return record_id


# ---------------------------------------------------------------------------
# Metric
# ---------------------------------------------------------------------------

async def upsert_metric(
    db: AsyncWsSurrealConnection,
    name: str,
    category: str = "financial",
) -> str:
    record_id = f"metric:{_slug(name)}"
    await db.upsert(record_id, {"name": name, "category": category})
    return record_id


# ---------------------------------------------------------------------------
# Transcript document
# ---------------------------------------------------------------------------

async def upsert_transcript_doc(
    db: AsyncWsSurrealConnection,
    company_id: str,
    quarter: int,
    year: int,
    date: str,
    raw_path: str,
) -> str:
    slug = _ticker_slug(company_id)
    record_id = f"transcript_doc:{slug}_{quarter}_{year}"
    await db.upsert(record_id, {
        "company": _rid(company_id),
        "quarter": quarter,
        "year": year,
        "date": _dt(date),
        "raw_path": raw_path,
        "processed": False,
    })
    return record_id


async def mark_transcript_processed(
    db: AsyncWsSurrealConnection,
    transcript_id: str,
) -> None:
    await db.merge(transcript_id, {"processed": True})


# ---------------------------------------------------------------------------
# Sentiment edge  (transcript_doc → metric)
# ---------------------------------------------------------------------------

async def upsert_sentiment_edge(
    db: AsyncWsSurrealConnection,
    transcript_id: str,
    metric_id: str,
    score: float,
    context: str,
    section: str,
) -> None:
    edge_id = f"expressed_sentiment:{_slug(transcript_id)}_{_slug(metric_id)}"
    await db.upsert(edge_id, {
        "in": _rid(transcript_id),
        "out": _rid(metric_id),
        "score": score,
        "context": context,
        "section": section,
    })


# ---------------------------------------------------------------------------
# Key metric snapshot  (FMP /api/v3/key-metrics)
# ---------------------------------------------------------------------------

async def upsert_key_metric_snapshot(
    db: AsyncWsSurrealConnection,
    company_id: str,
    period: str,
    dso: float | None,
    inventory_turnover: float | None,
    revenue_per_share: float | None,
    gross_profit_margin: float | None,
) -> str:
    record_id = f"key_metric_snapshot:{_ticker_slug(company_id)}_{_slug(period)}"
    await db.upsert(record_id, {
        "company": _rid(company_id),
        "period": period,
        "dso": dso,
        "inventory_turnover": inventory_turnover,
        "revenue_per_share": revenue_per_share,
        "gross_profit_margin": gross_profit_margin,
        "fetched_at": _now_dt(),
    })
    return record_id


# ---------------------------------------------------------------------------
# Revenue segment  (FMP /api/v3/revenue-product-segmentation)
# ---------------------------------------------------------------------------

async def upsert_revenue_segments(
    db: AsyncWsSurrealConnection,
    company_id: str,
    period: str,
    segments: list[dict],
) -> int:
    if not segments:
        return 0
    total_revenue = sum(float(s.get("revenue", 0)) for s in segments) or 1.0
    written = 0
    for seg in segments:
        name = seg.get("segment_name") or seg.get("name") or "unknown"
        revenue = float(seg.get("revenue", 0))
        pct = round(revenue / total_revenue * 100, 2)
        record_id = f"revenue_segment:{_ticker_slug(company_id)}_{_slug(period)}_{_slug(name)}"
        await db.upsert(record_id, {
            "company": _rid(company_id),
            "period": period,
            "segment_name": name,
            "revenue": revenue,
            "pct_of_total": pct,
            "fetched_at": _now_dt(),
        })
        written += 1
    return written


# ---------------------------------------------------------------------------
# Analyst target  (FMP /api/v3/price-target-consensus)
# ---------------------------------------------------------------------------

async def upsert_analyst_target(
    db: AsyncWsSurrealConnection,
    company_id: str,
    target_consensus: float | None,
    target_high: float | None,
    target_low: float | None,
    target_median: float | None,
) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y_%m_%d")
    record_id = f"analyst_target:{_ticker_slug(company_id)}_{date_str}"
    await db.upsert(record_id, {
        "company": _rid(company_id),
        "target_consensus": target_consensus,
        "target_high": target_high,
        "target_low": target_low,
        "target_median": target_median,
        "fetched_at": _now_dt(),
    })
    return record_id


# ---------------------------------------------------------------------------
# Guidance entry
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Backtesting — regime, price_gap_outcome, backtest_run, edges
# ---------------------------------------------------------------------------

async def upsert_regime(
    db: AsyncWsSurrealConnection,
    label: str,
    start_date: str,
    end_date: str,
    hmm_state_id: int,
    key_signals: list[str],
) -> str:
    record_id = f"regime:{_slug(label)}"
    await db.upsert(record_id, {
        "label": label,
        "start_date": _dt(start_date),
        "end_date": _dt(end_date),
        "hmm_state_id": hmm_state_id,
        "key_signals": key_signals,
    })
    return record_id


async def upsert_price_gap_outcome(
    db: AsyncWsSurrealConnection,
    company_id: str,
    transcript_id: str,
    regime_id: str,
    earnings_date: str,
    prev_close: float,
    next_open: float,
    gap_pct: float,
    gap_direction: str,
    is_extended_hours: bool,
    benchmark_return: float | None = None,
    relative_gap: float | None = None,
) -> str:
    slug = f"{_ticker_slug(company_id)}_{transcript_id.split(':')[-1]}"
    record_id = f"price_gap_outcome:{slug}"
    await db.upsert(record_id, {
        "company": _rid(company_id),
        "transcript": _rid(transcript_id),
        "regime": _rid(regime_id),
        "earnings_date": _dt(earnings_date),
        "prev_close": prev_close,
        "next_open": next_open,
        "gap_pct": gap_pct,
        "gap_direction": gap_direction,
        "benchmark_return": benchmark_return,
        "relative_gap": relative_gap,
        "is_extended_hours": is_extended_hours,
    })
    return record_id


async def upsert_backtest_run(
    db: AsyncWsSurrealConnection,
    run_id: str,
    ticker_universe: list[str],
    from_date: str,
    to_date: str,
    granularity: str = "quarter",
    sentiment_threshold: float = 0.2,
    benchmark: str = "SPY",
    include_ext_hours: bool = True,
    event_type: str = "Earnings",
) -> str:
    record_id = f"backtest_run:{_slug(run_id)}"
    await db.upsert(record_id, {
        "run_id": run_id,
        "run_at": _now_dt(),
        "ticker_universe": ticker_universe,
        "from_date": _dt(from_date),
        "to_date": _dt(to_date),
        "granularity": granularity,
        "sentiment_threshold": sentiment_threshold,
        "benchmark": benchmark,
        "include_ext_hours": include_ext_hours,
        "event_type": event_type,
        "directional_accuracy": None,
        "hit_rate_by_regime": None,
    })
    return record_id


async def upsert_occurred_during_edge(
    db: AsyncWsSurrealConnection,
    transcript_id: str,
    regime_id: str,
) -> None:
    edge_id = f"occurred_during:{transcript_id.split(':')[-1]}_{_slug(regime_id.split(':')[-1])}"
    await db.upsert(edge_id, {
        "in": _rid(transcript_id),
        "out": _rid(regime_id),
    })


async def merge_transcript_scores(
    db: AsyncWsSurrealConnection,
    transcript_id: str,
    confidence_score: float | None,
    analyst_pressure_index: float | None,
) -> None:
    updates: dict = {}
    if confidence_score is not None:
        updates["confidence_score"] = confidence_score
    if analyst_pressure_index is not None:
        updates["analyst_pressure_index"] = analyst_pressure_index
    if updates:
        await db.merge(transcript_id, updates)


async def upsert_predicted_by_edge(
    db: AsyncWsSurrealConnection,
    gap_outcome_id: str,
    run_record_id: str,
    signal_bundle: dict,
    predicted_direction: str,
    correct: bool,
) -> None:
    import json as _json
    edge_id = (
        f"predicted_by:{_slug(gap_outcome_id.split(':')[-1])}"
        f"_{_slug(run_record_id.split(':')[-1])}"
    )
    await db.upsert(edge_id, {
        "in": _rid(gap_outcome_id),
        "out": _rid(run_record_id),
        "composite_score": float(signal_bundle.get("composite_score", 0.0)),
        "signals_json": _json.dumps(signal_bundle.get("signals", [])),
        "predicted_direction": predicted_direction,
        "correct": correct,
    })


async def upsert_competes_with_edge(
    db: AsyncWsSurrealConnection,
    company_id: str,
    competitor_id: str,
    overlap: str,
) -> None:
    edge_id = f"competes_with:{_ticker_slug(company_id)}_{_ticker_slug(competitor_id)}"
    await db.upsert(edge_id, {
        "in": _rid(company_id),
        "out": _rid(competitor_id),
        "overlap": overlap,
    })


async def upsert_guidance_entry(
    db: AsyncWsSurrealConnection,
    company_id: str,
    metric_id: str,
    quarter: int,
    year: int,
    company_guide: float | None,
    analyst_est: float | None,
) -> str:
    record_id = (
        f"guidance_entry:{_ticker_slug(company_id)}"
        f"_{_slug(metric_id.split(':')[-1])}_{quarter}_{year}"
    )
    await db.upsert(record_id, {
        "company": _rid(company_id),
        "metric": _rid(metric_id),
        "quarter": quarter,
        "year": year,
        "company_guide": company_guide,
        "analyst_est": analyst_est,
    })
    return record_id
