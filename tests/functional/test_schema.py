"""Step 0.1 — Schema validation tests.

Verifies that all new SurrealDB tables (regime, price_gap_outcome, backtest_run,
occurred_during, predicted_by) and the new transcript_doc fields exist after
schema.surql is applied.

SurrealDB response shapes (Python SDK v2.x):
  INFO FOR DB   → dict  with key 'tables'  (not a list, key is 'tables' not 'tb')
  INFO FOR TABLE → dict with key 'fields'  (not a list, key is 'fields' not 'fd')

Run with:
    pytest tests/functional/test_schema.py -v
"""

import pytest
import pytest_asyncio


NEW_TABLES = [
    "regime",
    "price_gap_outcome",
    "backtest_run",
    "occurred_during",
    "predicted_by",
]

TABLE_REQUIRED_FIELDS: dict[str, list[str]] = {
    "regime": ["label", "start_date", "end_date", "hmm_state_id", "key_signals"],
    "price_gap_outcome": [
        "company", "transcript", "regime", "earnings_date",
        "prev_close", "next_open", "gap_pct", "gap_direction", "is_extended_hours",
    ],
    "backtest_run": [
        "run_id", "run_at", "ticker_universe", "from_date", "to_date",
        "granularity", "sentiment_threshold", "benchmark",
    ],
    "occurred_during": ["in", "out"],
    "predicted_by": ["in", "out", "predicted_direction", "correct"],
}

TRANSCRIPT_DOC_NEW_FIELDS = ["confidence_score", "analyst_pressure_index"]


@pytest.mark.asyncio
async def test_new_tables_exist(db):
    """All 5 new tables must be present in the DB after schema application."""
    result = await db.query("INFO FOR DB")
    # INFO FOR DB returns a dict directly; tables are under 'tables' key.
    assert isinstance(result, dict), f"Expected dict from INFO FOR DB, got {type(result)}"
    tables_info = result.get("tables", {})

    for table in NEW_TABLES:
        assert table in tables_info, (
            f"Table '{table}' not found in DB. Run: python -m src.db.init_schema\n"
            f"Available tables: {sorted(tables_info.keys())}"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("table,fields", TABLE_REQUIRED_FIELDS.items())
async def test_table_fields_exist(db, table: str, fields: list[str]):
    """Each required field must be defined on its table."""
    result = await db.query(f"INFO FOR TABLE {table}")
    # INFO FOR TABLE returns a dict directly; fields are under 'fields' key.
    assert isinstance(result, dict), f"Expected dict from INFO FOR TABLE {table}, got {type(result)}"
    field_defs = result.get("fields", {})

    for field in fields:
        assert field in field_defs, (
            f"Field '{field}' missing from table '{table}'. Check schema.surql.\n"
            f"Defined fields: {sorted(field_defs.keys())}"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", TRANSCRIPT_DOC_NEW_FIELDS)
async def test_transcript_doc_new_fields(db, field: str):
    """confidence_score and analyst_pressure_index must be defined on transcript_doc."""
    result = await db.query("INFO FOR TABLE transcript_doc")
    assert isinstance(result, dict)
    field_defs = result.get("fields", {})
    assert field in field_defs, (
        f"Field '{field}' missing from transcript_doc. "
        f"Ensure the schema extension was applied.\n"
        f"Defined fields: {sorted(field_defs.keys())}"
    )
