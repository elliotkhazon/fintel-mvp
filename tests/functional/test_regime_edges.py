"""Step 0.2c / 1.5 — occurred_during edge coverage tests.

SurrealDB response shapes (Python SDK v2.x):
  SELECT ... → list[dict] flat list of row dicts.

Run:
    pytest tests/functional/test_regime_edges.py -v
"""

import pytest

from tests.functional.conftest import query_count


@pytest.mark.asyncio
async def test_all_transcripts_have_regime_edge(db):
    """Every transcript_doc must have exactly 1 occurred_during edge."""
    rows = await db.query("SELECT id FROM transcript_doc")
    assert isinstance(rows, list)
    if not rows:
        pytest.skip("No transcript_doc rows found — run generate-synthetic first")

    transcript_ids = [r.get("id") for r in rows if isinstance(r, dict) and r.get("id")]

    orphaned = []
    multi_edge = []
    for tid in transcript_ids:
        edges = await db.query(
            f"SELECT id FROM occurred_during WHERE in = {tid}"
        )
        assert isinstance(edges, list)
        count = len(edges)
        if count == 0:
            orphaned.append(str(tid))
        elif count > 1:
            multi_edge.append((str(tid), count))

    assert not orphaned, (
        f"{len(orphaned)} transcript_doc(s) have NO occurred_during edge: "
        f"{orphaned[:5]}{'...' if len(orphaned) > 5 else ''}"
    )
    assert not multi_edge, (
        f"{len(multi_edge)} transcript_doc(s) have MULTIPLE occurred_during edges: "
        f"{multi_edge[:3]}"
    )


@pytest.mark.asyncio
async def test_occurred_during_points_to_valid_regime(db):
    """All occurred_during edges must point to an existing regime node."""
    rows = await db.query("SELECT in, out FROM occurred_during")
    assert isinstance(rows, list)
    if not rows:
        pytest.skip("No occurred_during edges found — run generate-synthetic first")

    bad_edges = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        regime_ref = row.get("out")
        if regime_ref is None:
            bad_edges.append(row)
            continue
        check = await db.query(f"SELECT id FROM {regime_ref}")
        assert isinstance(check, list)
        if not check:
            bad_edges.append(row)

    assert not bad_edges, (
        f"{len(bad_edges)} occurred_during edge(s) point to non-existent regime"
    )


@pytest.mark.asyncio
async def test_regime_assignment_matches_year(db):
    """Transcripts from 2016–2019 must link to GrowthExpansion."""
    rows = await db.query(
        "SELECT in.year AS year, out.label AS regime_label "
        "FROM occurred_during WHERE in.year IN [2016, 2017, 2018, 2019] LIMIT 20"
    )
    assert isinstance(rows, list)
    if not rows:
        pytest.skip("No transcripts for years 2016–2019 found")

    wrong = [
        r for r in rows
        if isinstance(r, dict) and r.get("regime_label") != "GrowthExpansion"
    ]
    assert not wrong, (
        f"Transcripts from 2016–2019 should map to GrowthExpansion, but found: {wrong[:3]}"
    )


@pytest.mark.asyncio
async def test_occurred_during_count_matches_transcript_count(db):
    """Number of occurred_during edges must equal number of transcript_doc rows."""
    transcripts = await query_count(db, "transcript_doc")
    edges = await query_count(db, "occurred_during")
    assert transcripts > 0, "No transcripts found"
    assert edges == transcripts, (
        f"Mismatch: {transcripts} transcript_doc rows but {edges} occurred_during edges"
    )
