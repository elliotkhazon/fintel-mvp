"""Apply schema.surql to the running SurrealDB instance.

Idempotent — safe to re-run. Uses DEFINE ... IF NOT EXISTS throughout.
Sends each statement individually (surrealdb v2.0.0 query() returns first result only).

Usage:
    python -m src.db.init_schema
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from src.db.connection import get_db


def _split_statements(sql: str) -> list[str]:
    """Split a .surql file on semicolons, skipping blank lines and comment-only lines."""
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        current.append(line)
        if stripped.endswith(";"):
            stmt = " ".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
    return statements


async def init_schema() -> int:
    """Execute all DDL statements one by one. Returns count of statements applied."""
    schema_path = Path(__file__).parent / "schema.surql"
    statements = _split_statements(schema_path.read_text(encoding="utf-8"))

    db = await get_db()
    ok = 0
    for stmt in statements:
        try:
            await db.query(stmt)
            ok += 1
        except Exception as exc:
            print(f"  [WARN] {exc} — SQL: {stmt[:80]}", file=sys.stderr)
    return ok


if __name__ == "__main__":
    count = asyncio.run(init_schema())
    print(f"Schema applied — {count} statement(s) OK.")
