"""Async SurrealDB singleton client — surrealdb v2.0.0 API.

SurrealDB runs in the k3s cluster (Docker Desktop) exposed via NodePort 30800.
URL resolution priority:
  1. SURREAL_URL env var  (explicit WebSocket URL, e.g. ws://localhost:30800/rpc)
  2. SURREAL_HTTP_ENDPOINT env var  (converted http:// → ws:// + /rpc appended)
  3. Hardcoded fallback: ws://localhost:30800/rpc
"""

from __future__ import annotations

import asyncio
import os

from surrealdb import AsyncSurreal
from surrealdb.connections.async_ws import AsyncWsSurrealConnection

_db: AsyncWsSurrealConnection | None = None
_lock = asyncio.Lock()


def _resolve_url() -> str:
    if url := os.getenv("SURREAL_URL"):
        return url
    if http := os.getenv("SURREAL_HTTP_ENDPOINT"):
        ws = http.replace("https://", "wss://").replace("http://", "ws://")
        return ws.rstrip("/") + "/rpc"
    return "ws://localhost:30800/rpc"


async def get_db() -> AsyncWsSurrealConnection:
    """Return the shared SurrealDB client, connecting on first call."""
    global _db
    async with _lock:
        if _db is None:
            url = _resolve_url()
            conn = AsyncSurreal(url)
            await conn.connect()
            await conn.signin({
                "username": os.getenv("SURREAL_USER", "root"),
                "password": os.getenv("SURREAL_PASS", "root"),
            })
            await conn.use(
                os.getenv("SURREAL_NS", "fintel"),
                os.getenv("SURREAL_DB", "earnings_model"),
            )
            _db = conn  # only assigned after full authenticated setup
    return _db


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None
