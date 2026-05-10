"""Shared fixtures for functional tests.

All functional tests run against a live local SurrealDB instance.
SurrealDB must be running on ws://localhost:30800/rpc (or SURREAL_URL env var).

Design notes:
- The db fixture resets the global _db singleton before each test so that
  the new connection binds to the correct per-test event loop (pytest-asyncio
  creates a fresh loop per test function by default).
- Schema is applied exactly once per pytest session via a module-level flag.
- RETURN 1 is used as the SurrealDB connectivity ping (SELECT 1 requires FROM).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

_schema_applied = False  # applied once per pytest session


@pytest_asyncio.fixture
async def db():
    """Yield a live SurrealDB connection, skipping if unreachable.

    Resets the global singleton before each test so the connection is bound
    to the current event loop (avoids 'attached to a different loop' errors).
    """
    global _schema_applied
    import src.db.connection as _conn

    # Force a fresh connection on the current event loop.
    _conn._db = None

    try:
        database = await _conn.get_db()
        await database.query("RETURN 1")   # SurrealDB ping (SELECT 1 needs FROM)
    except Exception as exc:
        pytest.skip(f"SurrealDB not reachable — skipping functional tests: {exc}")
        return

    if not _schema_applied:
        from src.db.init_schema import init_schema
        await init_schema()
        _schema_applied = True

    yield database

    # Close and reset so the next test gets a fresh connection on its loop.
    await _conn.close_db()


# ─── Test data helpers ─────────────────────────────────────────────────────────

SMOKE_TICKER = "SYN001"
TEST_TICKERS = [f"SYN{i+1:03d}" for i in range(5)]

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "transcripts"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def count_json_files(ticker: str | None = None) -> int:
    if not DATA_DIR.exists():
        return 0
    if ticker:
        target = DATA_DIR / ticker
        return len(list(target.glob("*.json"))) if target.exists() else 0
    return sum(len(list(d.glob("*.json"))) for d in DATA_DIR.iterdir() if d.is_dir())


async def query_count(db, table: str, where: str = "") -> int:
    """Return COUNT(*) for a table with an optional WHERE clause."""
    clause = f" WHERE {where}" if where else ""
    result = await db.query(f"SELECT count() FROM {table}{clause} GROUP ALL")
    rows = result[0] if isinstance(result, list) and result else []
    if isinstance(rows, list) and rows:
        return rows[0].get("count", 0)
    if isinstance(rows, dict):
        return rows.get("count", 0)
    return 0
