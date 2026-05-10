#!/usr/bin/env python3
"""Synthetic backtesting dataset generator — Phase 0, Tiers 1–4.

Generates SYN-prefixed ticker transcripts with pre-computed backtesting fields
and ingests them directly to SurrealDB (no LLM extraction step required).

Tier commands (run from project root):
  Tier 1 Smoke      : python scripts/generate_synthetic_backtest.py --tickers 1 --years 1 --start-year 2020
  Tier 2 NLP        : python scripts/generate_synthetic_backtest.py --tickers 5 --years 2 --start-year 2020 --use-llm
  Tier 3 Regime     : python scripts/generate_synthetic_backtest.py --tickers 20 --years 4 --start-year 2016
  Tier 4 Integration: python scripts/generate_synthetic_backtest.py --tickers 50 --years 5 --start-year 2018

Between tiers 1–4, wipe data/transcripts/ and reset SurrealDB before the next run.
Tier 5 (--tickers 500 --years 10 --start-year 2016) is run ONCE after Tier 4 passes.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from pathlib import Path

import click

# Allow imports from project root regardless of working directory.
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from src.db.connection import get_db
from src.db.normalizer import (
    mark_transcript_processed,
    merge_transcript_scores,
    upsert_company,
    upsert_occurred_during_edge,
    upsert_price_gap_outcome,
    upsert_regime,
    upsert_transcript_doc,
)
from src.models.regime_classifier import ALL_REGIME_LABELS, REGIME_META, classify_by_year

DATA_DIR = Path(__file__).parent.parent / "data" / "transcripts"

# ─── Regime configuration ────────────────────────────────────────────────────

REGIME_DISTRIBUTION = {
    "GrowthExpansion": {
        "gap_mu": 0.012,  "gap_sigma": 0.035,
        "conf_alpha": 7.0, "conf_beta": 2.7,    # Beta dist → mu ≈ 0.72
        "pressure_mode": 0.25,                   # triangular mode in [0,1]
    },
    "BlackSwan": {
        "gap_mu": -0.005, "gap_sigma": 0.080,
        "conf_alpha": 2.7, "conf_beta": 3.3,    # Beta dist → mu ≈ 0.45
        "pressure_mode": 0.60,
    },
    "HighInflation": {
        "gap_mu": 0.003,  "gap_sigma": 0.055,
        "conf_alpha": 3.3, "conf_beta": 2.7,    # Beta dist → mu ≈ 0.55
        "pressure_mode": 0.50,
    },
    "AIExpansion": {
        "gap_mu": 0.018,  "gap_sigma": 0.040,
        "conf_alpha": 6.8, "conf_beta": 3.2,    # Beta dist → mu ≈ 0.68
        "pressure_mode": 0.30,
    },
}

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

QUARTER_MONTH = {1: "02", 2: "05", 3: "08", 4: "11"}

# ─── Content templates ────────────────────────────────────────────────────────

_ASSERTIVE_PHRASES = [
    "We will deliver strong results",
    "We expect to achieve our targets",
    "We commit to accelerating growth",
    "We will expand margins",
    "We expect revenue to grow",
    "We will maintain our leadership position",
    "We commit to disciplined capital allocation",
    "We will deliver on our guidance",
]

_HEDGE_PHRASES = [
    "We believe conditions might improve",
    "We assume demand could recover gradually",
    "We estimate uncertainty may remain elevated",
    "We hope to see stabilization",
    "Margin pressure could persist",
    "We believe the environment might normalize",
    "We assume working capital may remain elevated",
    "We estimate recovery could take several quarters",
]

_GROWTH_QUESTIONS = [
    "Can you provide more color on the revenue growth trajectory in your core segment?",
    "What is your confidence level in achieving the full-year revenue guidance?",
    "How do you see pricing trends impacting your gross margins going forward?",
    "Can you elaborate on the market share gains you mentioned in your prepared remarks?",
    "What is driving the acceleration in your subscription revenue?",
]

_VOLATILE_QUESTIONS = [
    "What specific steps are you taking to address the supply chain disruptions?",
    "Can you elaborate on your supply chain remediation efforts and timeline?",
    "How do you plan to resolve the ongoing supply chain constraints?",
    "What is your liquidity position and how long can you sustain operations at current levels?",
    "Can you walk us through your contingency plans if conditions deteriorate further?",
]

_INFLATION_QUESTIONS = [
    "How much pricing power do you have to offset input cost inflation?",
    "Can you quantify the margin impact from raw material cost increases?",
    "What is your ability to pass through costs to customers without volume loss?",
    "How are you managing margin compression in the current environment?",
    "Can you talk about the pricing actions you are taking across your product lines?",
]

_AI_QUESTIONS = [
    "How are you thinking about AI infrastructure capex over the next 12 months?",
    "What is the expected ROI on your AI and data center investments?",
    "Can you talk about the GPU and data center demand trends you are seeing?",
    "How is AI adoption accelerating within your enterprise customer base?",
    "What is your competitive positioning in the AI-driven workload market?",
]

REGIME_QUESTIONS = {
    "GrowthExpansion": _GROWTH_QUESTIONS,
    "BlackSwan": _VOLATILE_QUESTIONS,
    "HighInflation": _INFLATION_QUESTIONS,
    "AIExpansion": _AI_QUESTIONS,
}


def _build_content(
    ticker: str,
    quarter: int,
    year: int,
    regime: str,
    guidance_text: str,
    analyst_pressure_index: float,
    is_qa_missing: bool,
) -> str:
    """Build a structured earnings call transcript string."""
    rng = random.Random(f"{ticker}{quarter}{year}")

    revenue = round(rng.uniform(2.5, 45.0), 1)
    eps = round(rng.uniform(0.50, 8.00), 2)
    gm = round(rng.uniform(35.0, 78.0), 1)
    gm_delta = round(rng.uniform(-3.0, 5.0), 1)

    cfg = REGIME_DISTRIBUTION[regime]

    # Build prepared remarks with regime-appropriate assertive/hedge density.
    if cfg["conf_alpha"] > cfg["conf_beta"]:
        # Assertive-heavy regime
        core_phrases = rng.sample(_ASSERTIVE_PHRASES, k=min(4, len(_ASSERTIVE_PHRASES)))
        extra_phrases = rng.sample(_HEDGE_PHRASES, k=1)
    else:
        # Hedge-heavy regime
        core_phrases = rng.sample(_HEDGE_PHRASES, k=min(4, len(_HEDGE_PHRASES)))
        extra_phrases = rng.sample(_ASSERTIVE_PHRASES, k=1)

    all_phrases = core_phrases + extra_phrases
    rng.shuffle(all_phrases)
    prepared_body = ". ".join(all_phrases) + "."

    qa_section = ""
    if not is_qa_missing:
        questions = REGIME_QUESTIONS.get(regime, _GROWTH_QUESTIONS)
        num_analysts = 4

        if analyst_pressure_index >= 0.6:
            # High pressure: repeat semantically similar questions
            base_q = questions[0]
            similar = questions[1] if len(questions) > 1 else questions[0]
            analyst_qs = [base_q, similar, similar[:80] + " specifically?", questions[-1]]
        else:
            analyst_qs = rng.sample(questions, k=min(num_analysts, len(questions)))

        qa_lines = []
        firms = ["Goldman Sachs", "Morgan Stanley", "JPMorgan", "Bank of America", "Citi"]
        names = ["Alex Chen", "Jordan Lee", "Morgan White", "Casey Brown", "Taylor Smith"]
        for i, q in enumerate(analyst_qs):
            analyst = names[i % len(names)]
            firm = firms[i % len(firms)]
            qa_lines.append(f"ANALYST {i+1} ({analyst}, {firm}): {q}")
            qa_lines.append(f"EXECUTIVE: Thank you. {rng.choice(_ASSERTIVE_PHRASES if cfg['conf_alpha'] > cfg['conf_beta'] else _HEDGE_PHRASES)}. I will provide more details in our next call.")
            qa_lines.append("")
        qa_section = "\n".join(qa_lines)
    else:
        qa_section = "(No analyst questions this quarter.)"

    return (
        f"OPERATOR: Good afternoon and welcome to {ticker} Q{quarter} {year} Earnings Call.\n\n"
        f"CEO PREPARED REMARKS:\n"
        f"Good afternoon. Revenue was ${revenue}B with EPS of ${eps}. {prepared_body}\n\n"
        f"CFO SECTION:\n"
        f"Gross margin was {gm}%, {'up' if gm_delta >= 0 else 'down'} {abs(gm_delta)}% year-over-year.\n\n"
        f"FORWARD GUIDANCE:\n"
        f"{guidance_text}\n\n"
        f"ANALYST Q&A:\n"
        f"{qa_section}"
    )


# ─── Record generation ────────────────────────────────────────────────────────

def _gap_direction(gap_pct: float) -> str:
    if gap_pct > 0.005:
        return "up"
    if gap_pct < -0.005:
        return "down"
    return "flat"


def _generate_record(
    ticker: str,
    quarter: int,
    year: int,
    use_llm: bool,
    seed: int,
) -> dict:
    """Generate a single synthetic transcript record dict."""
    rng = random.Random(seed)
    regime = classify_by_year(year)
    cfg = REGIME_DISTRIBUTION[regime]

    month = QUARTER_MONTH[quarter]
    earnings_date = f"{year}-{month}-15T17:00:00Z"

    # Edge case flags (seeded for reproducibility)
    is_mismatch = rng.random() < 0.05   # 5% sentiment/gap mismatch
    is_qa_missing = rng.random() < 0.02  # 2% missing Q&A

    # Price gap
    gap_pct = rng.gauss(cfg["gap_mu"], cfg["gap_sigma"])
    if is_mismatch:
        gap_pct = -gap_pct  # invert to create mismatch

    prev_close = round(rng.uniform(50.0, 500.0), 2)
    next_open = round(prev_close * (1 + gap_pct), 2)
    gap_direction = _gap_direction(gap_pct)

    benchmark_return = round(rng.gauss(0.0005, 0.008), 6)
    relative_gap = round(gap_pct - benchmark_return, 6)
    is_extended_hours = rng.random() < 0.70

    # NLP-derived scores (from distributions — overridden if --use-llm)
    confidence_score = round(rng.betavariate(cfg["conf_alpha"], cfg["conf_beta"]), 4)
    analyst_pressure_index: float | None
    if is_qa_missing:
        analyst_pressure_index = None
    else:
        analyst_pressure_index = round(
            rng.triangular(0.0, 1.0, cfg["pressure_mode"]), 4
        )

    # Guidance text (Faker-based; Gemini replaces if --use-llm)
    if is_mismatch and cfg["conf_alpha"] > cfg["conf_beta"]:
        guidance_text = "We believe conditions might remain challenging and assume near-term headwinds could persist."
    elif cfg["conf_alpha"] > cfg["conf_beta"]:
        guidance_text = f"We expect Q{(quarter%4)+1} revenue to grow year-over-year and will deliver on our full-year guidance."
    else:
        guidance_text = f"We estimate Q{(quarter%4)+1} could see modest improvement, though we assume conditions may remain uncertain."

    content = _build_content(
        ticker, quarter, year, regime, guidance_text,
        analyst_pressure_index or 0.0, is_qa_missing,
    )

    return {
        "symbol": ticker,
        "quarter": quarter,
        "year": year,
        "date": f"{year}-{month}-15T17:00:00Z",
        "content": content,
        "guidance_text": guidance_text,
        "confidence_score": confidence_score,
        "analyst_pressure_index": analyst_pressure_index,
        "regime": regime,
        "earnings_date": earnings_date,
        "prev_close": prev_close,
        "next_open": next_open,
        "gap_pct": round(gap_pct, 6),
        "gap_direction": gap_direction,
        "benchmark_return": benchmark_return,
        "relative_gap": relative_gap,
        "is_extended_hours": is_extended_hours,
        "_synthetic": True,
    }


def _enrich_with_llm(record: dict, ticker: str, quarter: int, year: int, regime: str) -> dict:
    """Replace guidance_text with Gemini output and recompute NLP scores."""
    try:
        from langchain_core.messages import HumanMessage
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            temperature=0.7,
        )
        prompt = (
            f"Write a short (2-3 sentence) earnings call forward guidance paragraph for "
            f"{ticker} Q{quarter} {year}. Market regime: {regime}. "
            f"Use realistic financial language appropriate for this regime. "
            f"Return only the paragraph text, no JSON or markdown."
        )
        guidance_text = llm.invoke([HumanMessage(content=prompt)]).content.strip()
        record["guidance_text"] = guidance_text
        record["content"] = _build_content(
            ticker, quarter, year, regime, guidance_text,
            record.get("analyst_pressure_index") or 0.0,
            record.get("analyst_pressure_index") is None,
        )
    except Exception as exc:
        click.echo(f"    [WARN] Gemini call failed for {ticker} Q{quarter} {year}: {exc}", err=True)

    # Run NLP models on the generated content.
    try:
        from src.models import confidence_scorer
        from src.models import analyst_pressure
        nlp_conf = confidence_scorer.score(record["content"])
        nlp_press = analyst_pressure.score(record["content"])
        record["confidence_score"] = nlp_conf
        if record["analyst_pressure_index"] is not None:
            record["analyst_pressure_index"] = nlp_press
    except Exception as exc:
        click.echo(f"    [WARN] NLP scoring failed: {exc}", err=True)

    return record


# ─── File I/O ─────────────────────────────────────────────────────────────────

def _write_json(record: dict) -> Path:
    ticker = record["symbol"]
    quarter = record["quarter"]
    year = record["year"]
    path = DATA_DIR / ticker / f"Q{quarter}_{year}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return path


# ─── SurrealDB ingest ─────────────────────────────────────────────────────────

async def _ingest_regimes(db) -> dict[str, str]:
    """Upsert all 4 regime nodes; return label → record_id mapping."""
    label_to_id: dict[str, str] = {}
    for label in ALL_REGIME_LABELS:
        meta = REGIME_META[label]
        rid = await upsert_regime(
            db,
            label=label,
            start_date=meta["start_date"],
            end_date=meta["end_date"],
            hmm_state_id=meta["hmm_state_id"],
            key_signals=meta["key_signals"],
        )
        label_to_id[label] = rid
    return label_to_id


async def _ingest_record(db, record: dict, path: Path, regime_id_map: dict[str, str]) -> None:
    ticker = record["symbol"]
    quarter = record["quarter"]
    year = record["year"]

    # Company
    sector, industry = SECTOR_POOL[
        (hash(ticker) % len(SECTOR_POOL))
    ]
    company_id = await upsert_company(db, ticker, ticker, sector=sector, industry=industry)

    # Transcript doc
    transcript_id = await upsert_transcript_doc(
        db,
        company_id=company_id,
        quarter=quarter,
        year=year,
        date=record["date"],
        raw_path=str(path),
    )

    # NLP scores
    await merge_transcript_scores(
        db,
        transcript_id=transcript_id,
        confidence_score=record.get("confidence_score"),
        analyst_pressure_index=record.get("analyst_pressure_index"),
    )

    # Regime edge
    regime_label = record["regime"]
    regime_id = regime_id_map.get(regime_label, f"regime:{regime_label.lower()}")
    await upsert_occurred_during_edge(db, transcript_id, regime_id)

    # Price gap outcome
    await upsert_price_gap_outcome(
        db,
        company_id=company_id,
        transcript_id=transcript_id,
        regime_id=regime_id,
        earnings_date=record["earnings_date"],
        prev_close=record["prev_close"],
        next_open=record["next_open"],
        gap_pct=record["gap_pct"],
        gap_direction=record["gap_direction"],
        is_extended_hours=record["is_extended_hours"],
        benchmark_return=record.get("benchmark_return"),
        relative_gap=record.get("relative_gap"),
    )

    # Mark transcript processed
    await mark_transcript_processed(db, transcript_id)


# ─── Main generation logic ────────────────────────────────────────────────────

async def generate_synthetic(
    num_tickers: int,
    num_years: int,
    start_year: int,
    use_llm: bool,
) -> int:
    """Generate and ingest synthetic backtesting records. Returns record count."""
    tickers = [f"SYN{i+1:03d}" for i in range(num_tickers)]
    years = list(range(start_year, start_year + num_years))
    total = num_tickers * num_years * 4
    click.echo(
        f"Generating {num_tickers} tickers × {num_years} years × 4 quarters "
        f"= {total} records (start_year={start_year}, use_llm={use_llm})"
    )

    # Phase 1: generate and write JSON files.
    records_written: list[tuple[dict, Path]] = []
    seed_base = start_year * 10_000
    for t_idx, ticker in enumerate(tickers):
        for year in years:
            for quarter in range(1, 5):
                seed = seed_base + t_idx * 1_000 + year * 10 + quarter
                record = _generate_record(ticker, quarter, year, use_llm, seed)
                if use_llm:
                    record = _enrich_with_llm(record, ticker, quarter, year, record["regime"])
                path = _write_json(record)
                records_written.append((record, path))
    click.secho(f"  Wrote {len(records_written)} JSON files to {DATA_DIR}", fg="green")

    # Phase 2: ingest to SurrealDB.
    click.echo("  Ingesting to SurrealDB ...")
    db = await get_db()
    regime_id_map = await _ingest_regimes(db)
    ingested = errors = 0
    for record, path in records_written:
        try:
            await _ingest_record(db, record, path, regime_id_map)
            ingested += 1
        except Exception as exc:
            click.secho(
                f"    [ERROR] {record['symbol']} Q{record['quarter']} {record['year']}: {exc}",
                fg="red", err=True,
            )
            errors += 1

    click.secho(
        f"  Ingested {ingested}/{len(records_written)} records ({errors} errors).",
        fg="green" if errors == 0 else "yellow",
    )
    return ingested


# ─── Standalone CLI ───────────────────────────────────────────────────────────

@click.command("generate-synthetic")
@click.option("--tickers", "num_tickers", required=True, type=int, help="Number of synthetic tickers")
@click.option("--years", "num_years", required=True, type=int, help="Number of years to cover")
@click.option("--start-year", required=True, type=int, help="First year to generate")
@click.option("--use-llm", is_flag=True, default=False,
              help="Use Gemini for guidance_text + run NLP models (Tier 2 only)")
def cli_generate(num_tickers: int, num_years: int, start_year: int, use_llm: bool):
    """Generate synthetic earnings transcripts and ingest to SurrealDB."""
    count = asyncio.run(generate_synthetic(num_tickers, num_years, start_year, use_llm))
    click.secho(f"\nDone — {count} records ingested.", fg="green")


if __name__ == "__main__":
    cli_generate()
