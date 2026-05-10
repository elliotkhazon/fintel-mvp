"""Multi-hop SurrealQL retrieval functions — surrealdb v2.0.0.

In v2.0.0, db.query() returns the result list directly (not wrapped in [{"result": ...}]).
All functions return plain Python lists/dicts.
"""

from __future__ import annotations

from surrealdb.connections.async_ws import AsyncWsSurrealConnection


def _rows(result) -> list[dict]:
    """Normalise a query result to a list of dicts regardless of shape."""
    if result is None:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return [result]
    return []


# ---------------------------------------------------------------------------
# Hop 1 — company sentiment
# ---------------------------------------------------------------------------

async def hop1_sentiment(
    db: AsyncWsSurrealConnection,
    ticker: str,
    last_n_quarters: int = 4,
) -> list[dict]:
    """Fetch expressed_sentiment edges for a company over the last N quarters."""
    sql = """
        SELECT
            in.quarter AS quarter,
            in.year AS year,
            out.name AS metric,
            score,
            context,
            section
        FROM expressed_sentiment
        WHERE in.company.ticker = $ticker
        ORDER BY in.year DESC, in.quarter DESC
        LIMIT $limit
        FETCH in, out
    """
    result = await db.query(sql, {"ticker": ticker.upper(), "limit": last_n_quarters * 10})
    return _rows(result)


# ---------------------------------------------------------------------------
# Hop 2 — competitor signals
# ---------------------------------------------------------------------------

async def hop2_competitor_signals(
    db: AsyncWsSurrealConnection,
    ticker: str,
) -> list[dict]:
    """Traverse competes_with → fetch recent sentiment from rivals."""
    sql = """
        SELECT
            out.ticker AS competitor,
            out.name AS competitor_name,
            (
                SELECT score, context, section, in.quarter AS quarter, in.year AS year
                FROM expressed_sentiment
                WHERE in.company = out
                ORDER BY in.year DESC, in.quarter DESC
                LIMIT 5
            ) AS signals
        FROM competes_with
        WHERE in.ticker = $ticker
        FETCH out
    """
    result = await db.query(sql, {"ticker": ticker.upper()})
    return _rows(result)


# ---------------------------------------------------------------------------
# Hop 3 — supplier signals
# ---------------------------------------------------------------------------

async def hop3_supplier_signals(
    db: AsyncWsSurrealConnection,
    ticker: str,
) -> list[dict]:
    """Traverse supplied_by → fetch revenue/demand signals from suppliers."""
    sql = """
        SELECT
            out.ticker AS supplier,
            out.name AS supplier_name,
            materiality,
            (
                SELECT score, context, section, in.quarter AS quarter, in.year AS year
                FROM expressed_sentiment
                WHERE in.company = out
                  AND out.name IN ["Revenue", "Gross Margin", "Backlog", "Demand", "Shipments"]
                ORDER BY in.year DESC, in.quarter DESC
                LIMIT 5
            ) AS signals
        FROM supplied_by
        WHERE in.ticker = $ticker
        FETCH out
    """
    result = await db.query(sql, {"ticker": ticker.upper()})
    return _rows(result)


# ---------------------------------------------------------------------------
# Fundamental data queries
# ---------------------------------------------------------------------------

async def fetch_key_metrics_history(
    db: AsyncWsSurrealConnection,
    ticker: str,
    last_n: int = 5,
) -> list[dict]:
    sql = """
        SELECT period, dso, inventory_turnover, revenue_per_share, gross_profit_margin
        FROM key_metric_snapshot
        WHERE company = type::thing('company', $ticker_slug)
        ORDER BY period DESC
        LIMIT $n
    """
    result = await db.query(sql, {"ticker_slug": ticker.lower(), "n": last_n})
    return _rows(result)


async def fetch_segments(
    db: AsyncWsSurrealConnection,
    ticker: str,
    last_n_periods: int = 2,
) -> list[dict]:
    sql = """
        SELECT period, segment_name, revenue, pct_of_total
        FROM revenue_segment
        WHERE company = type::thing('company', $ticker_slug)
        ORDER BY period DESC
        LIMIT $n
    """
    result = await db.query(sql, {"ticker_slug": ticker.lower(), "n": last_n_periods * 10})
    return _rows(result)


async def fetch_analyst_targets(
    db: AsyncWsSurrealConnection,
    ticker: str,
) -> dict:
    sql = """
        SELECT target_consensus, target_high, target_low, target_median, fetched_at
        FROM analyst_target
        WHERE company = type::thing('company', $ticker_slug)
        ORDER BY fetched_at DESC
        LIMIT 1
    """
    result = await db.query(sql, {"ticker_slug": ticker.lower()})
    rows = _rows(result)
    return rows[0] if rows else {}


async def fetch_guidance(
    db: AsyncWsSurrealConnection,
    ticker: str,
    quarter: int,
    year: int,
) -> dict:
    sql = """
        SELECT metric.name AS metric_name, company_guide, analyst_est, actual, beat
        FROM guidance_entry
        WHERE company.ticker = $ticker AND quarter = $q AND year = $y
        LIMIT 1
    """
    result = await db.query(sql, {"ticker": ticker.upper(), "q": quarter, "y": year})
    rows = _rows(result)
    return rows[0] if rows else {}


# ---------------------------------------------------------------------------
# Company graph (first-degree summary)
# ---------------------------------------------------------------------------

async def fetch_company_graph(
    db: AsyncWsSurrealConnection,
    ticker: str,
) -> dict:
    company_result = await db.query(
        "SELECT ticker, name, sector, industry FROM company WHERE ticker = $ticker",
        {"ticker": ticker.upper()},
    )
    companies = _rows(company_result)
    if not companies:
        return {}
    company = companies[0]

    async def _tickers(sql: str) -> list[str]:
        r = await db.query(sql, {"ticker": ticker.upper()})
        return [row.get("ticker", "") for row in _rows(r) if row.get("ticker")]

    competitors = await _tickers(
        "SELECT out.ticker AS ticker FROM competes_with WHERE in.ticker = $ticker FETCH out"
    )
    suppliers = await _tickers(
        "SELECT out.ticker AS ticker FROM supplied_by WHERE in.ticker = $ticker FETCH out"
    )
    customers = await _tickers(
        "SELECT out.ticker AS ticker FROM sold_to WHERE in.ticker = $ticker FETCH out"
    )
    sentiment = await hop1_sentiment(db, ticker, last_n_quarters=2)

    return {
        **company,
        "competitors": competitors,
        "suppliers": suppliers,
        "customers": customers,
        "recent_sentiment": sentiment,
    }
