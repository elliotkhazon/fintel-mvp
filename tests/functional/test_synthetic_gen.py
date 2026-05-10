"""Step 0.2 — Synthetic data generator tests.

SurrealDB response shapes (Python SDK v2.x):
  SELECT ...  → list[dict]  (flat list of row dicts, NOT nested)
  INFO FOR *  → dict directly

Run Tier 1 gate:
    pytest tests/functional/test_synthetic_gen.py -k smoke -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from tests.functional.conftest import DATA_DIR, SMOKE_TICKER, count_json_files, query_count

REQUIRED_JSON_FIELDS = [
    "symbol", "quarter", "year", "date", "content", "guidance_text",
    "regime", "earnings_date", "prev_close", "next_open",
    "gap_pct", "gap_direction", "benchmark_return", "relative_gap",
    "is_extended_hours", "_synthetic",
]

VALID_REGIMES = {"GrowthExpansion", "BlackSwan", "HighInflation", "AIExpansion"}
VALID_DIRECTIONS = {"up", "down", "flat"}


# ─── Smoke tests (Tier 1 exit gate) ──────────────────────────────────────────

@pytest.mark.smoke
def test_smoke_json_files_exist():
    """Tier 1: SYN001 directory must contain exactly 4 JSON files."""
    ticker_dir = DATA_DIR / SMOKE_TICKER
    assert ticker_dir.exists(), (
        f"data/transcripts/{SMOKE_TICKER}/ not found. "
        f"Run: python scripts/datamanager.py generate-synthetic --tickers 1 --years 1 --start-year 2020"
    )
    files = list(ticker_dir.glob("*.json"))
    assert len(files) >= 4, f"Expected at least 4 files, found {len(files)}: {[f.name for f in files]}"


@pytest.mark.smoke
def test_smoke_json_schema_valid():
    """Tier 1: Every JSON file must have all required fields with correct types."""
    ticker_dir = DATA_DIR / SMOKE_TICKER
    if not ticker_dir.exists():
        pytest.skip("SYN001 transcripts not generated yet")

    for json_file in sorted(ticker_dir.glob("*.json")):
        with open(json_file) as f:
            record = json.load(f)

        for field in REQUIRED_JSON_FIELDS:
            assert field in record, f"{json_file.name}: missing field '{field}'"

        assert record["_synthetic"] is True
        assert record["gap_direction"] in VALID_DIRECTIONS, (
            f"Invalid gap_direction: {record['gap_direction']}"
        )
        assert record["regime"] in VALID_REGIMES, (
            f"Invalid regime: {record['regime']}"
        )
        assert isinstance(record["prev_close"], (int, float))
        assert isinstance(record["next_open"], (int, float))
        assert isinstance(record["gap_pct"], (int, float))
        assert isinstance(record["is_extended_hours"], bool)


@pytest.mark.smoke
def test_smoke_gap_pct_formula():
    """Tier 1: gap_pct must be consistent with prev_close / next_open."""
    ticker_dir = DATA_DIR / SMOKE_TICKER
    if not ticker_dir.exists():
        pytest.skip("SYN001 transcripts not generated yet")

    for json_file in ticker_dir.glob("*.json"):
        with open(json_file) as f:
            rec = json.load(f)
        computed = (rec["next_open"] - rec["prev_close"]) / rec["prev_close"]
        assert abs(computed - rec["gap_pct"]) < 1e-4, (
            f"{json_file.name}: gap_pct mismatch — stored={rec['gap_pct']:.6f}, "
            f"computed={computed:.6f}"
        )


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_smoke_db_records_exist(db):
    """Tier 1: SurrealDB must contain at least 4 price_gap_outcome rows."""
    count = await query_count(db, "price_gap_outcome")
    assert count >= 4, (
        f"Expected at least 4 price_gap_outcome rows, got {count}. "
        f"Run generate-synthetic --tickers 1 --years 1 --start-year 2020"
    )


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_smoke_transcripts_processed(db):
    """Tier 1: All transcript_doc rows must have processed=True."""
    total = await query_count(db, "transcript_doc")
    processed = await query_count(db, "transcript_doc", "processed = true")
    assert total > 0, "No transcript_doc rows found"
    assert total == processed, (
        f"{total - processed} transcripts not processed (processed={processed}/{total})"
    )


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_smoke_no_null_required_fields(db):
    """Tier 1: No null values in mandatory price_gap_outcome fields."""
    rows = await db.query(
        "SELECT * FROM price_gap_outcome WHERE "
        "prev_close IS NONE OR next_open IS NONE "
        "OR gap_pct IS NONE OR gap_direction IS NONE LIMIT 1"
    )
    # SELECT returns list[dict] directly
    assert isinstance(rows, list)
    assert len(rows) == 0, (
        f"Found {len(rows)} price_gap_outcome row(s) with null required fields"
    )


# ─── Regime distribution tests (Tier 3+) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_all_regime_nodes_present(db):
    """All 4 regime nodes must exist in SurrealDB."""
    rows = await db.query("SELECT label FROM regime")
    assert isinstance(rows, list)
    labels = {r.get("label") for r in rows if isinstance(r, dict)}
    for expected in VALID_REGIMES:
        assert expected in labels, (
            f"Regime node '{expected}' not found. Run generate-synthetic first."
        )


@pytest.mark.asyncio
async def test_gap_direction_within_threshold():
    """gap_direction must agree with gap_pct sign for all generated JSON files."""
    if not DATA_DIR.exists():
        pytest.skip("No transcripts generated yet")

    mismatches = []
    for json_file in DATA_DIR.rglob("*.json"):
        with open(json_file) as f:
            rec = json.load(f)
        pct = rec.get("gap_pct", 0)
        direction = rec.get("gap_direction")
        if pct > 0.005 and direction != "up":
            mismatches.append(f"{json_file.name}: gap_pct={pct:.4f} but direction={direction}")
        elif pct < -0.005 and direction != "down":
            mismatches.append(f"{json_file.name}: gap_pct={pct:.4f} but direction={direction}")

    assert not mismatches, "gap_direction mismatches:\n" + "\n".join(mismatches[:10])


@pytest.mark.asyncio
async def test_confidence_score_in_range(db):
    """confidence_score must be in [0, 1] for all transcript_doc rows that have it."""
    rows = await db.query(
        "SELECT confidence_score FROM transcript_doc WHERE confidence_score IS NOT NONE"
    )
    assert isinstance(rows, list)
    out_of_range = [
        r for r in rows
        if isinstance(r, dict) and isinstance(r.get("confidence_score"), (int, float))
        and not (0.0 <= r["confidence_score"] <= 1.0)
    ]
    assert not out_of_range, (
        f"{len(out_of_range)} confidence_score values out of [0, 1] range"
    )


@pytest.mark.asyncio
async def test_analyst_pressure_in_range(db):
    """analyst_pressure_index must be in [0, 1] when non-null."""
    rows = await db.query(
        "SELECT analyst_pressure_index FROM transcript_doc "
        "WHERE analyst_pressure_index IS NOT NONE"
    )
    assert isinstance(rows, list)
    out_of_range = [
        r for r in rows
        if isinstance(r, dict) and isinstance(r.get("analyst_pressure_index"), (int, float))
        and not (0.0 <= r["analyst_pressure_index"] <= 1.0)
    ]
    assert not out_of_range, (
        f"{len(out_of_range)} analyst_pressure_index values out of [0, 1] range"
    )
