"""Step 4 functional test — BacktestAgent end-to-end smoke test.

Runs a backtest on SYN001 + SYN002 for year 2018 (8 events, no LLM calls).

Pass criteria (from build order §8 Step 4):
  - backtest_run row written to SurrealDB
  - directional_accuracy ∈ [0, 1]
  - no null signal_bundle on predicted_by edges

SurrealDB must be running on ws://localhost:30800 (or SURREAL_URL env var).
Run: pytest tests/functional/test_backtest_agent.py -v
"""

from __future__ import annotations

import pytest

from tests.functional.conftest import query_count

SMOKE_TICKERS = ["SYN001", "SYN002"]
FROM_DATE = "2018-01-01T00:00:00Z"
TO_DATE = "2018-12-31T23:59:59Z"

# Populated by test_backtest_run_and_metrics — subsequent tests skip if None.
_run_id: str | None = None          # original UUID stored in run_id field
_run_record_id: str | None = None   # actual SurrealDB record key (slugged)


@pytest.mark.asyncio
async def test_backtest_run_and_metrics(db):
    """End-to-end: run backtest on 2 tickers × 1 year, check returned metrics."""
    global _run_id, _run_record_id
    from src.agents.backtest_agent import run_backtest

    result = await run_backtest(
        ticker_universe=SMOKE_TICKERS,
        from_date=FROM_DATE,
        to_date=TO_DATE,
        sentiment_threshold=0.2,
        with_report=False,
    )

    _run_id = result.get("run_id")
    _run_record_id = result.get("run_record_id")

    assert result.get("error") is None, f"Backtest error: {result.get('error')}"
    assert result["total_processed"] > 0, "No events were processed"

    acc = result.get("directional_accuracy")
    assert acc is not None, "directional_accuracy is None"
    assert 0.0 <= acc <= 1.0, f"directional_accuracy={acc} out of [0, 1]"

    hit_rates = result.get("hit_rate_by_regime") or {}
    assert len(hit_rates) > 0, "hit_rate_by_regime is empty"
    for label, rate in hit_rates.items():
        assert 0.0 <= rate <= 1.0, f"hit_rate[{label}]={rate} out of [0, 1]"


@pytest.mark.asyncio
async def test_signal_bundles_not_null(db):
    """Every result row must have a non-null signal_bundle with composite_score."""
    if _run_id is None:
        pytest.skip("Depends on test_backtest_run_and_metrics")
    from src.agents.backtest_agent import run_backtest

    result = await run_backtest(
        ticker_universe=SMOKE_TICKERS,
        from_date=FROM_DATE,
        to_date=TO_DATE,
        sentiment_threshold=0.2,
        with_report=False,
        run_id=_run_id + "_check",
    )
    for r in result.get("results", []):
        bundle = r.get("signal_bundle")
        assert bundle is not None, f"signal_bundle is None for {r.get('transcript_id')}"
        assert "composite_score" in bundle, "signal_bundle missing composite_score"


@pytest.mark.asyncio
async def test_backtest_run_row_in_db(db):
    """backtest_run row must be written and directional_accuracy persisted."""
    if _run_id is None:
        pytest.skip("Depends on test_backtest_run_and_metrics")

    count = await query_count(db, "backtest_run", f"run_id = '{_run_id}'")
    assert count == 1, f"Expected 1 backtest_run row for run_id={_run_id}, got {count}"

    rows = await db.query(
        "SELECT directional_accuracy FROM backtest_run WHERE run_id = $run_id",
        {"run_id": _run_id},
    )
    records = rows if isinstance(rows, list) else []
    assert records, "backtest_run row not found in DB"
    acc = records[0].get("directional_accuracy")
    assert acc is not None, "directional_accuracy not persisted to DB"
    assert 0.0 <= acc <= 1.0


@pytest.mark.asyncio
async def test_predicted_by_edges_in_db(db):
    """predicted_by edges must be written to SurrealDB for each event."""
    if _run_record_id is None:
        pytest.skip("Depends on test_backtest_run_and_metrics")

    rows = await db.query(
        f"SELECT count() FROM predicted_by WHERE out = {_run_record_id} GROUP ALL"
    )
    records = rows if isinstance(rows, list) else []
    count = records[0].get("count", 0) if records else 0
    assert count > 0, "No predicted_by edges written for this backtest run"


@pytest.mark.asyncio
async def test_occurred_during_edges_created(db):
    """occurred_during edges must be created for each processed transcript."""
    if _run_id is None:
        pytest.skip("Depends on test_backtest_run_and_metrics")

    # At least some occurred_during edges should exist after the backtest
    count = await query_count(db, "occurred_during")
    assert count > 0, "No occurred_during edges found after backtest run"


@pytest.mark.asyncio
async def test_price_gap_outcomes_in_db(db):
    """price_gap_outcome rows must be written for each processed event."""
    if _run_id is None:
        pytest.skip("Depends on test_backtest_run_and_metrics")

    count = await query_count(db, "price_gap_outcome")
    assert count > 0, "No price_gap_outcome rows written"


@pytest.mark.asyncio
async def test_regime_nodes_exist(db):
    """All 4 regime nodes must be in DB after backtest run."""
    from src.models.regime_classifier import ALL_REGIME_LABELS

    rows = await db.query("SELECT label FROM regime")
    records = rows if isinstance(rows, list) else []
    labels = {r.get("label") for r in records if isinstance(r, dict)}
    missing = set(ALL_REGIME_LABELS) - labels
    assert not missing, f"Missing regime nodes: {missing}"
