#!/usr/bin/env python3
"""Data manager CLI — generate and manage synthetic FMP transcripts."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import click
import httpx


DATA_DIR = Path(__file__).parent.parent / "data" / "transcripts"
FMP_BASE = "http://localhost:8000"


@click.group()
def cli():
    """Manage synthetic earnings call transcripts for the FMP Mock API."""
    pass


@cli.command()
@click.option("--symbol", required=True, help="Stock ticker symbol, e.g. AAPL")
@click.option("--quarter", required=True, type=click.IntRange(1, 4), help="Fiscal quarter (1-4)")
@click.option("--year", required=True, type=int, help="Fiscal year, e.g. 2024")
@click.option("--force", is_flag=True, default=False, help="Regenerate even if transcript already exists")
def generate(symbol: str, quarter: int, year: int, force: bool):
    """Generate a single synthetic earnings call transcript via Gemini."""
    from src.agents.transcript_agent import get_transcript, _transcript_path

    path = _transcript_path(symbol.upper(), quarter, year)

    if path.exists() and not force:
        click.echo(f"Already exists: {path}  (use --force to regenerate)")
        return

    if force and path.exists():
        path.unlink()
        click.echo(f"Deleted existing transcript at {path}")

    click.echo(f"Generating {symbol.upper()} Q{quarter} {year} ...")
    try:
        transcript = get_transcript(symbol, quarter, year)
        click.secho(f"Saved → {path}", fg="green")
        preview = transcript.get("content", "")[:200].replace("\n", " ")
        click.echo(f"Preview: {preview}...")
    except Exception as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        sys.exit(1)


@cli.command("bulk-generate")
@click.option("--symbol", required=True, help="Stock ticker symbol")
@click.option("--start-year", required=True, type=int, help="First year to generate")
@click.option("--end-year", required=True, type=int, help="Last year to generate (inclusive)")
@click.option("--force", is_flag=True, default=False, help="Regenerate existing transcripts")
def bulk_generate(symbol: str, start_year: int, end_year: int, force: bool):
    """Bulk-generate all quarters for a symbol across a year range."""
    from src.agents.transcript_agent import get_transcript, _transcript_path

    total = (end_year - start_year + 1) * 4
    done = 0

    for year in range(start_year, end_year + 1):
        for quarter in range(1, 5):
            path = _transcript_path(symbol.upper(), quarter, year)
            if path.exists() and not force:
                click.echo(f"  Skip {symbol.upper()} Q{quarter} {year} (exists)")
                done += 1
                continue
            if force and path.exists():
                path.unlink()
            click.echo(f"  Generating {symbol.upper()} Q{quarter} {year} ({done+1}/{total}) ...")
            try:
                get_transcript(symbol, quarter, year)
                click.secho(f"  Done.", fg="green")
            except Exception as exc:
                click.secho(f"  Error: {exc}", fg="red", err=True)
            done += 1

    click.echo(f"\nCompleted {done}/{total} transcripts for {symbol.upper()}.")


@cli.command("list")
@click.option("--symbol", default=None, help="Filter output to a specific symbol")
def list_transcripts(symbol: Optional[str] = None):
    """List all available transcripts in the data store."""
    if not DATA_DIR.exists():
        click.echo("No data directory found — run `generate` first.")
        return

    count = 0
    for sym_dir in sorted(DATA_DIR.iterdir()):
        if not sym_dir.is_dir():
            continue
        if symbol and sym_dir.name.upper() != symbol.upper():
            continue
        files = sorted(sym_dir.glob("*.json"))
        if files:
            click.echo(f"\n{sym_dir.name}/")
        for f in files:
            size_kb = f.stat().st_size / 1024
            click.echo(f"  {f.name}  ({size_kb:.1f} KB)")
            count += 1

    click.echo(f"\nTotal: {count} transcript(s)")


@cli.command()
@click.argument("symbol")
@click.argument("quarter", type=int)
@click.argument("year", type=int)
def show(symbol: str, quarter: int, year: int):
    """Print the content of a specific transcript."""
    from src.agents.transcript_agent import _transcript_path

    path = _transcript_path(symbol.upper(), quarter, year)
    if not path.exists():
        click.secho(f"Not found: {path}", fg="red", err=True)
        sys.exit(1)

    with open(path) as f:
        data = json.load(f)

    click.echo(f"Symbol : {data['symbol']}")
    click.echo(f"Quarter: Q{data['quarter']} {data['year']}")
    click.echo(f"Date   : {data['date']}")
    click.echo("-" * 60)
    click.echo(data.get("content", ""))


@cli.command()
@click.argument("symbol")
@click.argument("quarter", type=int)
@click.argument("year", type=int)
def delete(symbol: str, quarter: int, year: int):
    """Delete a specific transcript."""
    from src.agents.transcript_agent import _transcript_path

    path = _transcript_path(symbol.upper(), quarter, year)
    if not path.exists():
        click.secho(f"Not found: {path}", fg="yellow", err=True)
        return
    path.unlink()
    click.secho(f"Deleted: {path}", fg="green")


@cli.command()
@click.option("--symbol", required=True, help="Stock ticker symbol")
@click.option("--period", default="quarter", type=click.Choice(["annual", "quarter"]), help="Reporting period")
def fetch_metrics(symbol: str, period: str):
    """Pull key metrics (DSO, Inventory Turnover) → key_metric_snapshot in SurrealDB."""
    async def _run():
        from src.db.connection import get_db
        from src.db.normalizer import upsert_company, upsert_key_metric_snapshot

        sym = symbol.upper()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{FMP_BASE}/api/v3/key-metrics/{sym}", params={"period": period})
        if resp.status_code != 200:
            click.secho(f"FMP error {resp.status_code}: {resp.text}", fg="red", err=True)
            return

        data = resp.json()
        rows = data if isinstance(data, list) else [data]
        db = await get_db()
        company_id = await upsert_company(db, sym, sym)
        written = 0
        for row in rows:
            raw_date = row.get("date", "")
            period_str = raw_date[:7] if raw_date else row.get("period", period)
            await upsert_key_metric_snapshot(
                db, company_id, period_str,
                dso=row.get("daysOfSalesOutstanding") or row.get("dso"),
                inventory_turnover=row.get("inventoryTurnover"),
                revenue_per_share=row.get("revenuePerShare"),
                gross_profit_margin=row.get("grossProfitMargin"),
            )
            written += 1
        click.secho(f"Upserted {written} key_metric_snapshot row(s) for {sym}.", fg="green")

    asyncio.run(_run())


@cli.command()
@click.option("--symbol", required=True, help="Stock ticker symbol")
def fetch_segments(symbol: str):
    """Pull revenue product segmentation → revenue_segment in SurrealDB."""
    async def _run():
        from src.db.connection import get_db
        from src.db.normalizer import upsert_company, upsert_revenue_segments

        sym = symbol.upper()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{FMP_BASE}/api/v3/revenue-product-segmentation/{sym}")
        if resp.status_code != 200:
            click.secho(f"FMP error {resp.status_code}: {resp.text}", fg="red", err=True)
            return

        raw = resp.json()
        db = await get_db()
        company_id = await upsert_company(db, sym, sym)

        if isinstance(raw, dict):
            segments = [{"segment_name": k, "revenue": v} for k, v in raw.items() if isinstance(v, (int, float))]
            period = "latest"
        elif isinstance(raw, list) and raw:
            first = raw[0]
            segments = [{"segment_name": k, "revenue": v} for k, v in first.items()
                        if k != "date" and isinstance(v, (int, float))]
            period = first.get("date", "latest")[:7]
        else:
            segments = []
            period = "latest"

        written = await upsert_revenue_segments(db, company_id, period, segments)
        click.secho(f"Upserted {written} revenue_segment row(s) for {sym}.", fg="green")

    asyncio.run(_run())


@cli.command()
@click.option("--symbol", required=True, help="Stock ticker symbol")
def fetch_price_targets(symbol: str):
    """Pull analyst price target consensus → analyst_target in SurrealDB."""
    async def _run():
        from src.db.connection import get_db
        from src.db.normalizer import upsert_company, upsert_analyst_target

        sym = symbol.upper()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{FMP_BASE}/api/v3/price-target-consensus/{sym}")
        if resp.status_code != 200:
            click.secho(f"FMP error {resp.status_code}: {resp.text}", fg="red", err=True)
            return

        data = resp.json()
        row = data[0] if isinstance(data, list) and data else data
        db = await get_db()
        company_id = await upsert_company(db, sym, sym)
        await upsert_analyst_target(
            db, company_id,
            target_consensus=row.get("targetConsensus"),
            target_high=row.get("targetHigh"),
            target_low=row.get("targetLow"),
            target_median=row.get("targetMedian"),
        )
        click.secho(f"Upserted analyst_target for {sym} (consensus={row.get('targetConsensus')}).", fg="green")

    asyncio.run(_run())


@cli.command()
@click.option("--symbol", required=True, help="Stock ticker symbol")
def fetch_fundamentals(symbol: str):
    """Run fetch-metrics, fetch-segments, and fetch-price-targets in one pass."""
    click.echo(f"Fetching all fundamentals for {symbol.upper()} ...")
    ctx = click.get_current_context()
    ctx.invoke(fetch_metrics, symbol=symbol, period="quarter")
    ctx.invoke(fetch_segments, symbol=symbol)
    ctx.invoke(fetch_price_targets, symbol=symbol)


@cli.command()
@click.option("--symbol", default=None, help="Limit to a specific symbol (default: all)")
def ingest(symbol: Optional[str]):
    """Run extraction agent on all unprocessed local transcripts → SurrealDB graph."""
    from src.agents.extraction_agent import run_extraction

    sym_dirs = []
    if symbol:
        d = DATA_DIR / symbol.upper()
        if d.exists():
            sym_dirs = [d]
        else:
            click.secho(f"No transcripts found for {symbol.upper()}.", fg="yellow")
            return
    else:
        sym_dirs = [d for d in DATA_DIR.iterdir() if d.is_dir()] if DATA_DIR.exists() else []

    total = ingested = skipped = errors = 0
    for sym_dir in sorted(sym_dirs):
        for f in sorted(sym_dir.glob("*.json")):
            parts = f.stem.split("_")  # Q1_2024 → ["Q1", "2024"]
            if len(parts) != 2:
                continue
            try:
                q = int(parts[0].lstrip("Q"))
                y = int(parts[1])
            except ValueError:
                continue
            total += 1
            click.echo(f"  Ingesting {sym_dir.name} Q{q} {y} ...")
            try:
                result = asyncio.run(run_extraction(sym_dir.name, q, y))
                if result.get("error"):
                    click.secho(f"    Error: {result['error']}", fg="red")
                    errors += 1
                else:
                    click.secho(f"    Done.", fg="green")
                    ingested += 1
            except Exception as exc:
                click.secho(f"    Exception: {exc}", fg="red", err=True)
                errors += 1

    click.echo(f"\nIngestion complete: {ingested} ingested, {skipped} skipped, {errors} errors (total {total}).")


@cli.command("generate-synthetic")
@click.option("--tickers", "num_tickers", required=True, type=int,
              help="Number of synthetic tickers (SYN001..SYNN)")
@click.option("--years", "num_years", required=True, type=int,
              help="Number of years per ticker")
@click.option("--start-year", required=True, type=int,
              help="First year to generate (e.g. 2016)")
@click.option("--use-llm", is_flag=True, default=False,
              help="Enable Gemini for guidance_text + NLP scoring (Tier 2 only)")
def generate_synthetic_cmd(num_tickers: int, num_years: int, start_year: int, use_llm: bool):
    """Generate synthetic backtesting transcripts and ingest to SurrealDB.

    \b
    Tier 1 Smoke     : --tickers 1 --years 1 --start-year 2020
    Tier 2 NLP       : --tickers 5 --years 2 --start-year 2020 --use-llm
    Tier 3 Regime    : --tickers 20 --years 4 --start-year 2016
    Tier 4 Integration: --tickers 50 --years 5 --start-year 2018

    Between tiers 1-4 reset with:
      Remove-Item data\\transcripts -Recurse -Force
      surreal sql "REMOVE DATABASE earnings_model"
    """
    import sys as _sys
    _scripts = str(Path(__file__).parent)
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    from generate_synthetic_backtest import generate_synthetic
    count = asyncio.run(generate_synthetic(num_tickers, num_years, start_year, use_llm))
    click.secho(f"\nDone — {count} records ingested.", fg="green")


@cli.command("fit-hmm")
@click.option("--from", "from_date", default="2010-01-01", show_default=True,
              help="Training window start (YYYY-MM-DD)")
@click.option("--to", "to_date", default="2026-12-31", show_default=True,
              help="Training window end (YYYY-MM-DD)")
@click.option("--synthetic", is_flag=True, default=False,
              help="Generate synthetic macro data instead of fetching from FMP (no API required)")
def fit_hmm_cmd(from_date: str, to_date: str, synthetic: bool):
    """Fit the 4-state market regime HMM and save models/hmm_regime.pkl.

    \b
    Phase 0 (pipeline validation):
      python scripts/datamanager.py fit-hmm --synthetic
      Generates VIX / Fed Funds / CPI observations from regime distributions
      and fits the HMM — no FMP access required.

    Phase 1 (production):
      python scripts/datamanager.py fit-hmm --from 2010-01-01 --to 2026-12-31
      Fetches real macro data from FMP and refits. Replaces the synthetic model.
    """
    if not synthetic:
        click.secho(
            "Real FMP macro fitting is a Phase 1 task — not yet implemented.\n"
            "Use --synthetic to fit on generated data for pipeline validation.",
            fg="yellow",
        )
        sys.exit(1)

    from_year = int(from_date[:4])
    to_year = int(to_date[:4])
    click.echo(f"Fitting HMM on synthetic macro data ({from_year}–{to_year}) ...")
    try:
        from src.models.regime_classifier import MODEL_PATH, fit_synthetic_hmm
        out = fit_synthetic_hmm(output_path=MODEL_PATH, from_year=from_year, to_year=to_year)
        click.secho(f"Model saved → {out}", fg="green")
        click.echo("Run `pytest tests/functional/test_regime_classifier.py -v` to validate.")
    except ImportError as exc:
        click.secho(f"Missing dependency: {exc}\nRun: pip install hmmlearn", fg="red", err=True)
        sys.exit(1)


@cli.command("process-nlp")
@click.option("--symbol", default=None, help="Limit to a specific symbol (default: all)")
@click.option("--force", is_flag=True, default=False,
              help="Re-process rows that already have confidence_score set")
def process_nlp_cmd(symbol: Optional[str], force: bool):
    """Phase 1 — bulk-run NLP models on all transcript_doc rows and write scores to SurrealDB.

    \b
    Always runs:
      confidence_scorer  (spaCy assertive/hedge verb ratio → confidence_score)
      analyst_pressure   (SBERT Q&A clustering → analyst_pressure_index)

    Runs when model is available:
      finbert_extractor  (ProsusAI/finbert → expressed_sentiment edge, section=prepared)

    Reads transcript content from raw_path JSON files written by generate-synthetic.
    Skips rows where confidence_score is already set unless --force is passed.
    """
    async def _run():
        from src.db.connection import get_db
        from src.db.normalizer import (
            merge_transcript_scores,
            upsert_metric,
            upsert_sentiment_edge,
        )
        from src.models import analyst_pressure as ap
        from src.models import confidence_scorer as cs
        from src.models import finbert_extractor as fb

        db = await get_db()

        rows = await db.query("SELECT id, raw_path, confidence_score FROM transcript_doc")
        records = rows[0]["result"] if (rows and rows[0].get("result") is not None) else []

        if symbol:
            sym_lower = symbol.lower()
            records = [r for r in records if sym_lower in str(r.get("id", "")).lower()]

        total = len(records)
        click.echo(f"Found {total} transcript_doc row(s) to process.")

        finbert_on = fb.is_available()
        finbert_metric_id: Optional[str] = None
        if finbert_on:
            click.echo("FinBERT available — will create expressed_sentiment edges.")
            finbert_metric_id = await upsert_metric(
                db, "Management Confidence", category="nlp_finbert"
            )
        else:
            click.echo(
                "FinBERT not available — running confidence_scorer + analyst_pressure only."
            )

        done = skipped = errors = 0
        for row in records:
            transcript_id = str(row["id"])
            raw_path = row.get("raw_path")

            if not force and row.get("confidence_score") is not None:
                skipped += 1
                continue

            if not raw_path:
                click.secho(f"  {transcript_id}: no raw_path — skipping", fg="yellow")
                errors += 1
                continue

            path = Path(raw_path)
            if not path.exists():
                click.secho(f"  {transcript_id}: file not found ({raw_path}) — skipping", fg="yellow")
                errors += 1
                continue

            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                content = data.get("content", "")

                conf = cs.score(content)
                press = ap.score(content)
                await merge_transcript_scores(db, transcript_id, conf, press)

                if finbert_on and finbert_metric_id:
                    mean_sent = fb.mean_sentiment(content)
                    if mean_sent is not None:
                        await upsert_sentiment_edge(
                            db, transcript_id, finbert_metric_id,
                            score=mean_sent,
                            context="FinBERT bulk process",
                            section="prepared",
                        )
                done += 1
            except Exception as exc:
                click.secho(f"  {transcript_id}: {exc}", fg="red", err=True)
                errors += 1

        click.secho(
            f"\nDone — {done} processed, {skipped} skipped (already set), {errors} errors "
            f"(total {total}).",
            fg="green" if errors == 0 else "yellow",
        )

    asyncio.run(_run())


@cli.command()
@click.option("--ticker", "tickers", multiple=True,
              help="Ticker to include (repeat for multiple, e.g. --ticker SYN001 --ticker SYN002)")
@click.option("--universe", default=None, type=click.Choice(["all"]),
              help="Predefined universe: 'all' = all tickers in data/transcripts")
@click.option("--from", "from_date", required=True,
              help="Start date YYYY-MM-DD (inclusive)")
@click.option("--to", "to_date", required=True,
              help="End date YYYY-MM-DD (inclusive)")
@click.option("--threshold", "sentiment_threshold", default=0.2, type=float, show_default=True,
              help="Composite score cutoff for directional prediction")
@click.option("--benchmark", default="SPY", show_default=True)
@click.option("--with-report", is_flag=True, default=False,
              help="Call Gemini generate_report for each event (~1 LLM call per event)")
@click.option("--run-id", default=None, help="Explicit run UUID (auto-generated if omitted)")
def backtest(tickers, universe, from_date, to_date, sentiment_threshold, benchmark,
             with_report, run_id):
    """Run backtesting agent across a ticker universe.

    \b
    Smoke (2 tickers, 1 year):
      python scripts/datamanager.py backtest --ticker SYN001 --ticker SYN002 \\
        --from 2018-01-01 --to 2018-12-31

    Full 50-ticker universe:
      python scripts/datamanager.py backtest --universe all \\
        --from 2018-01-01 --to 2022-12-31

    With LLM reports (~1 Gemini call per event):
      python scripts/datamanager.py backtest --universe all \\
        --from 2018-01-01 --to 2022-12-31 --with-report
    """
    async def _run():
        from src.agents.backtest_agent import run_backtest

        if universe == "all":
            ticker_universe = [
                d.name for d in sorted(DATA_DIR.iterdir()) if d.is_dir()
            ] if DATA_DIR.exists() else []
        elif tickers:
            ticker_universe = [t.upper() for t in tickers]
        else:
            click.secho("Provide --ticker or --universe.", fg="red", err=True)
            sys.exit(1)

        from_iso = f"{from_date}T00:00:00Z"
        to_iso = f"{to_date}T23:59:59Z"

        click.echo(
            f"Running backtest on {len(ticker_universe)} ticker(s) "
            f"[{from_date} to {to_date}], threshold={sentiment_threshold} ..."
        )
        if with_report:
            click.echo(f"  LLM reports enabled — up to {len(ticker_universe)} Gemini calls.")

        result = await run_backtest(
            ticker_universe=ticker_universe,
            from_date=from_iso,
            to_date=to_iso,
            sentiment_threshold=sentiment_threshold,
            benchmark=benchmark,
            with_report=with_report,
            run_id=run_id,
        )

        if result.get("error"):
            click.secho(f"Error: {result['error']}", fg="red", err=True)
            sys.exit(1)

        click.secho(
            f"\nRun ID   : {result['run_id']}\n"
            f"Processed: {result['total_processed']} events\n"
            f"Accuracy : {result.get('directional_accuracy')}\n"
            f"By regime: {result.get('hit_rate_by_regime')}",
            fg="green",
        )
        # Write run_id to .last_run_id for convenience
        (Path(__file__).parent.parent / ".last_run_id").write_text(result["run_id"])

    asyncio.run(_run())


@cli.command("backtest-report")
@click.option("--run-id", required=True, help="UUID of a completed backtest_run")
@click.option("--stratify-by", default=None, type=click.Choice(["regime"]),
              help="Stratify output by regime")
def backtest_report(run_id: str, stratify_by: Optional[str]):
    """Print Layer 4 metrics for a completed backtest run.

    \b
    Example:
      python scripts/datamanager.py backtest-report --run-id <UUID>
      python scripts/datamanager.py backtest-report --run-id <UUID> --stratify-by regime
    """
    import json as _json
    import sys as _sys
    _eval = str(Path(__file__).parent.parent)
    if _eval not in _sys.path:
        _sys.path.insert(0, _eval)
    from eval.evaluators.backtest_eval import run_evaluation

    try:
        result = run_evaluation(run_id)
    except ValueError as exc:
        click.secho(str(exc), fg="red", err=True)
        sys.exit(1)

    if stratify_by == "regime":
        output = {
            "run_id": result["run_id"],
            "total_predictions": result["total_predictions"],
            "directional_accuracy": result["directional_accuracy"],
            "hit_rate_by_regime": result.get("hit_rate_by_regime", {}),
        }
    else:
        output = result

    click.echo(_json.dumps(output, indent=2))


@cli.command("seed-financials")
@click.option("--tickers", "num_tickers", default=50, show_default=True, type=int,
              help="Number of SYN tickers to seed (SYN001…SYNN)")
def seed_financials_cmd(num_tickers: int):
    """Seed key_metric_snapshot, revenue_segment, analyst_target, guidance_entry,
    and competes_with edges for synthetic SYN tickers.

    \b
    Run after generate-synthetic and before backtest:
      python scripts/datamanager.py seed-financials --tickers 50
    """
    import sys as _sys
    _scripts = str(Path(__file__).parent)
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    from seed_synthetic_financials import seed_financials
    counts = asyncio.run(seed_financials(num_tickers))
    click.secho("\nSeed complete:", fg="green")
    for table, n in counts.items():
        click.echo(f"  {table:25s} {n:>5} rows")


@cli.command()
def seed():
    """Seed supplier/competitor/customer edges from config/supply_chains.json."""
    async def _run():
        from src.db.connection import get_db
        from src.db.relationship_seeder import seed_relationships
        db = await get_db()
        counts = await seed_relationships(db)
        click.secho(
            f"Seeded: {counts['competes_with']} competes_with, "
            f"{counts['supplied_by']} supplied_by, {counts['sold_to']} sold_to.",
            fg="green",
        )

    asyncio.run(_run())


if __name__ == "__main__":
    cli()
