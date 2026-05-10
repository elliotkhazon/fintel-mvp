#!/usr/bin/env python3
"""Seed synthetic financial data for SYN tickers into SurrealDB.

Populates five tables missing for synthetic tickers so all 7 signal-bundle
components compute non-zero, correlated scores:

  key_metric_snapshot  → dso_trend, inventory_velocity
  revenue_segment      → segment_mix_shift
  analyst_target       → analyst_target_gap
  guidance_entry       → guidance_gap, analyst_target_gap
  competes_with edges  → laggard_signal (hop2)

All values are:
  - Deterministic (seeded by ticker + period hash)
  - Correlated with confidence_score from each transcript JSON so that
    signals align with gap_direction in ~60-65% of events
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from src.db.connection import get_db
from src.db.normalizer import (
    upsert_analyst_target,
    upsert_competes_with_edge,
    upsert_guidance_entry,
    upsert_key_metric_snapshot,
    upsert_metric,
    upsert_revenue_segments,
)

DATA_DIR = Path(__file__).parent.parent / "data" / "transcripts"

# Must match generate_synthetic_backtest.py exactly
SECTOR_POOL = [
    ("Technology", "Semiconductors"),
    ("Technology", "Software"),
    ("Healthcare", "Biotechnology"),
    ("Financials", "Banking"),
    ("Consumer Discretionary", "Retail"),
    ("Industrials", "Aerospace & Defense"),
    ("Energy", "Oil & Gas"),
    ("Materials", "Chemicals"),
]

INDUSTRY_SEGMENTS: dict[str, list[str]] = {
    "Semiconductors":      ["Compute", "Memory", "Networking"],
    "Software":            ["Enterprise", "Cloud", "Professional Services"],
    "Biotechnology":       ["Oncology", "Immunology", "Gene Therapy"],
    "Banking":             ["Retail Banking", "Investment Banking", "Asset Management"],
    "Retail":              ["Physical Stores", "E-commerce", "Private Label"],
    "Aerospace & Defense": ["Defense Systems", "Commercial Aviation", "Services"],
    "Oil & Gas":           ["Upstream", "Downstream", "Midstream"],
    "Chemicals":           ["Specialty Chemicals", "Commodity Chemicals", "Performance Materials"],
}


def _read_transcripts(ticker: str) -> dict[tuple[int, int], dict]:
    """Return {(quarter, year): record} for all JSON files in the ticker's directory."""
    out: dict[tuple[int, int], dict] = {}
    ticker_dir = DATA_DIR / ticker
    if not ticker_dir.exists():
        return out
    for f in sorted(ticker_dir.glob("Q*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            q, y = int(rec["quarter"]), int(rec["year"])
            out[(q, y)] = rec
        except Exception:
            pass
    return out


async def seed_financials(num_tickers: int) -> dict[str, int]:
    """Generate and upsert synthetic financial data for SYN001…SYN{N}."""
    tickers = [f"SYN{i + 1:03d}" for i in range(num_tickers)]

    ticker_sector: dict[str, tuple[str, str]] = {
        t: SECTOR_POOL[hash(t) % len(SECTOR_POOL)] for t in tickers
    }

    industry_groups: dict[str, list[str]] = defaultdict(list)
    for ticker, (_, industry) in ticker_sector.items():
        industry_groups[industry].append(ticker)

    db = await get_db()

    # Shared metric node used by guidance_entry (revenue guidance).
    revenue_metric_id = await upsert_metric(db, "Revenue", category="financial")

    counts: dict[str, int] = {
        "key_metric_snapshot": 0,
        "revenue_segment": 0,
        "analyst_target": 0,
        "guidance_entry": 0,
        "competes_with": 0,
    }

    click.echo(f"Seeding financial data for {num_tickers} synthetic tickers …")

    for ticker in tickers:
        company_id = f"company:{ticker.lower()}"
        _, industry = ticker_sector[ticker]
        seg_labels = INDUSTRY_SEGMENTS.get(industry, ["Core", "Adjacent", "Other"])

        transcripts = _read_transcripts(ticker)
        if not transcripts:
            click.secho(f"  [{ticker}] No transcript files — skipping.", fg="yellow")
            continue

        sorted_periods = sorted(transcripts.keys())  # [(q, y), …] chronologically
        rng = random.Random(hash(ticker))

        # Per-ticker baseline metrics (deterministic, stable across runs)
        base_dso = rng.uniform(30.0, 70.0)
        base_inv = rng.uniform(3.0, 12.0)
        base_rps = rng.uniform(5.0, 50.0)      # revenue per share baseline
        base_gpm = rng.uniform(0.30, 0.75)     # gross profit margin baseline
        base_target = rng.uniform(80.0, 350.0) # analyst price-target baseline

        all_conf = [float(transcripts[p].get("confidence_score", 0.5)) for p in sorted_periods]
        mean_conf = sum(all_conf) / len(all_conf)

        start_year = sorted_periods[0][1]

        # ── per-period data ───────────────────────────────────────────────────
        for idx, (q, y) in enumerate(sorted_periods):
            rec = transcripts[(q, y)]
            conf = float(rec.get("confidence_score", 0.5))
            period_str = f"{y}-Q{q}"
            years_elapsed = (y - start_year) + (q - 1) / 4.0

            # key_metric_snapshot ─────────────────────────────────────────────
            # High confidence → lower DSO (faster cash collection)
            dso = round(base_dso - (conf - 0.5) * 10.0 + rng.gauss(0, 1.5), 1)
            # High confidence → higher inventory turnover (strong demand)
            inv = round(max(0.5, base_inv + (conf - 0.5) * 2.0 + rng.gauss(0, 0.3)), 2)
            rps = round(
                base_rps * (1.0 + years_elapsed * 0.04) + (conf - 0.5) * 2.0 + rng.gauss(0, 0.5),
                2,
            )
            gpm = round(
                max(0.10, min(0.95, base_gpm + (conf - 0.5) * 0.06 + rng.gauss(0, 0.015))),
                4,
            )
            await upsert_key_metric_snapshot(
                db, company_id, period_str,
                dso=dso, inventory_turnover=inv,
                revenue_per_share=rps, gross_profit_margin=gpm,
            )
            counts["key_metric_snapshot"] += 1

            # revenue_segment ─────────────────────────────────────────────────
            # Top segment grows faster in bullish periods (divergence from blended)
            base_total = base_rps * 1_000_000 * (1.0 + years_elapsed * 0.06)
            top_share = max(0.40, min(0.70, 0.55 + (conf - 0.5) * 0.08))
            mid_share = max(0.15, min(0.40, 0.30 - (conf - 0.5) * 0.04))
            bot_share = max(0.05, 1.0 - top_share - mid_share)
            segs = [
                {"segment_name": seg, "revenue": round(base_total * share, 0)}
                for seg, share in zip(seg_labels, [top_share, mid_share, bot_share])
            ]
            counts["revenue_segment"] += await upsert_revenue_segments(
                db, company_id, period_str, segs
            )

            # guidance_entry ──────────────────────────────────────────────────
            # analyst_est = normalised baseline (100); company_guide is conservative
            # when bullish (below 100) and aggressive when bearish (above 100).
            # Both in the same scale so analyst_target_gap can compare them.
            analyst_est = 100.0
            guide_factor = 1.0 - (conf - 0.5) * 0.15   # 0.925 … 1.075
            company_guide = round(analyst_est * guide_factor, 2)
            await upsert_guidance_entry(
                db, company_id, revenue_metric_id, q, y,
                company_guide=company_guide, analyst_est=analyst_est,
            )
            counts["guidance_entry"] += 1

        # ── analyst_target (one record per ticker) ────────────────────────────
        # target_consensus is in the same ~100-scale as company_guide so the
        # analyst_target_gap ratio is interpretable.
        target_consensus = round(100.0 * (1.0 + (mean_conf - 0.5) * 0.30), 2)
        await upsert_analyst_target(
            db, company_id,
            target_consensus=target_consensus,
            target_high=round(target_consensus * 1.15, 2),
            target_low=round(target_consensus * 0.85, 2),
            target_median=round((target_consensus + target_consensus * 1.15) / 2.0, 2),
        )
        counts["analyst_target"] += 1

        click.echo(f"  [{ticker}] {len(sorted_periods)} periods — done.")

    # ── competes_with edges (intra-industry, up to 3 rivals per ticker) ───────
    click.echo("Seeding competes_with edges …")
    for industry, group in industry_groups.items():
        for ticker in group:
            company_id = f"company:{ticker.lower()}"
            rivals = [t for t in group if t != ticker]
            rng_comp = random.Random(f"{ticker}_comp")
            selected = rng_comp.sample(rivals, min(3, len(rivals)))
            for rival in selected:
                try:
                    await upsert_competes_with_edge(
                        db, company_id, f"company:{rival.lower()}", overlap=industry
                    )
                    counts["competes_with"] += 1
                except Exception as exc:
                    click.secho(f"  competes_with {ticker}→{rival}: {exc}", fg="yellow")

    return counts


async def _main(num_tickers: int) -> None:
    counts = await seed_financials(num_tickers)
    click.secho("\nSeed complete:", fg="green")
    for table, n in counts.items():
        click.echo(f"  {table:25s} {n:>5} rows")


@click.command("seed-financials")
@click.option("--tickers", "num_tickers", default=50, show_default=True, type=int,
              help="Number of SYN tickers to seed (SYN001 … SYNN)")
def cli_seed(num_tickers: int):
    """Seed key_metric_snapshot, revenue_segment, analyst_target, guidance_entry,
    and competes_with edges for synthetic SYN tickers.

    \b
    Run AFTER generate-synthetic and BEFORE backtest:
      python scripts/seed_synthetic_financials.py --tickers 50
    """
    asyncio.run(_main(num_tickers))


if __name__ == "__main__":
    cli_seed()
