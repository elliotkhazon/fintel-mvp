"""Seed cross-company graph edges from config/supply_chains.json.

Creates `competes_with`, `supplied_by`, `sold_to`, and `lead_indicator_for` edges.
Idempotent: uses db.upsert() so re-running is safe.
"""

from __future__ import annotations

import json
from pathlib import Path

from surrealdb.connections.async_ws import AsyncWsSurrealConnection

from src.db.normalizer import _rid, _slug, upsert_company, upsert_metric

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "supply_chains.json"


def _edge_id(table: str, from_id: str, to_id: str) -> str:
    return f"{table}:{_slug(from_id)}_{_slug(to_id)}"


async def _upsert_edge(
    db: AsyncWsSurrealConnection,
    table: str,
    from_id: str,
    to_id: str,
    fields: dict,
) -> None:
    eid = _edge_id(table, from_id, to_id)
    await db.upsert(eid, {"in": _rid(from_id), "out": _rid(to_id), **fields})


async def seed_relationships(db: AsyncWsSurrealConnection) -> dict[str, int]:
    """Seed edges for all companies in supply_chains.json.

    Returns counts: {"competes_with": n, "supplied_by": n, "sold_to": n}
    """
    if not CONFIG_PATH.exists():
        return {"competes_with": 0, "supplied_by": 0, "sold_to": 0}

    config: dict = json.loads(CONFIG_PATH.read_text())
    counts = {"competes_with": 0, "supplied_by": 0, "sold_to": 0}
    revenue_metric_id = await upsert_metric(db, "Revenue", "financial")

    for ticker, relations in config.items():
        primary_id = await upsert_company(db, ticker, ticker)

        for comp_ticker in relations.get("competitors", []):
            comp_id = await upsert_company(db, comp_ticker, comp_ticker)
            await _upsert_edge(db, "competes_with", primary_id, comp_id, {"overlap": "end_market"})
            await _upsert_edge(db, "competes_with", comp_id, primary_id, {"overlap": "end_market"})
            counts["competes_with"] += 2

        for sup_ticker in relations.get("suppliers", []):
            sup_id = await upsert_company(db, sup_ticker, sup_ticker)
            await _upsert_edge(db, "supplied_by", primary_id, sup_id, {"materiality": "primary"})
            eid = _edge_id("lead_indicator_for", sup_id, primary_id)
            await db.upsert(eid, {
                "in": _rid(sup_id),
                "out": _rid(primary_id),
                "metric": _rid(revenue_metric_id),
                "lag_quarters": 1,
            })
            counts["supplied_by"] += 1

        for cust_ticker in relations.get("customers", []):
            cust_id = await upsert_company(db, cust_ticker, cust_ticker)
            await _upsert_edge(db, "sold_to", primary_id, cust_id, {"materiality": "primary"})
            counts["sold_to"] += 1

    return counts
