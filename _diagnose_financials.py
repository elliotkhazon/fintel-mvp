"""Diagnostic: check what financial seed data exists in SurrealDB for SYN tickers."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

async def main():
    from src.db.connection import get_db
    db = await get_db()

    ticker = "SYN001"

    # 1. key_metric_snapshot
    r = await db.query("SELECT count() FROM key_metric_snapshot GROUP ALL")
    print(f"key_metric_snapshot total rows: {r}")

    r = await db.query(
        "SELECT period, dso, inventory_turnover FROM key_metric_snapshot "
        "WHERE company.ticker = $t LIMIT 3",
        {"t": ticker}
    )
    print(f"key_metric_snapshot for {ticker} (sample): {r}")

    # Direct RecordID lookup
    r2 = await db.query(
        "SELECT period, dso FROM key_metric_snapshot "
        "WHERE company = company:syn001 LIMIT 3"
    )
    print(f"key_metric_snapshot direct RecordID lookup (sample): {r2}")

    # type::thing() lookup (the new query pattern)
    r3 = await db.query(
        "SELECT period, dso FROM key_metric_snapshot "
        "WHERE company = type::thing('company', $slug) LIMIT 3",
        {"slug": "syn001"}
    )
    print(f"key_metric_snapshot type::thing lookup (sample): {r3}")

    # 2. revenue_segment
    r = await db.query("SELECT count() FROM revenue_segment GROUP ALL")
    print(f"\nrevenue_segment total rows: {r}")

    r = await db.query(
        "SELECT period, segment_name, revenue FROM revenue_segment "
        "WHERE company.ticker = $t LIMIT 3",
        {"t": ticker}
    )
    print(f"revenue_segment for {ticker} (sample): {r}")

    # 3. analyst_target
    r = await db.query("SELECT count() FROM analyst_target GROUP ALL")
    print(f"\nanalyst_target total rows: {r}")

    r = await db.query(
        "SELECT target_consensus, fetched_at FROM analyst_target "
        "WHERE company.ticker = $t LIMIT 3",
        {"t": ticker}
    )
    print(f"analyst_target for {ticker} (sample): {r}")

    # 4. guidance_entry
    r = await db.query("SELECT count() FROM guidance_entry GROUP ALL")
    print(f"\nguidance_entry total rows: {r}")

    r = await db.query(
        "SELECT quarter, year, company_guide, analyst_est FROM guidance_entry "
        "WHERE company.ticker = $t LIMIT 3",
        {"t": ticker}
    )
    print(f"guidance_entry for {ticker} (sample): {r}")

    # 5. competes_with
    r = await db.query("SELECT count() FROM competes_with GROUP ALL")
    print(f"\ncompetes_with total rows: {r}")

asyncio.run(main())
