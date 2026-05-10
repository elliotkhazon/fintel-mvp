#!/usr/bin/env python3
"""End-to-end backtest runner + report generator.

Runs the backtesting pipeline, evaluates all Layer 4 metrics,
and prints a formatted human-readable report.

Usage:
  python scripts/run_backtest_report.py --universe all \\
      --from 2018-01-01 --to 2022-12-31 --threshold 0.1

  python scripts/run_backtest_report.py --ticker SYN001 --ticker SYN002 \\
      --from 2018-01-01 --to 2018-12-31 --out report.txt
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import click

DATA_DIR = Path(__file__).parent.parent / "data" / "transcripts"

SIGNAL_WEIGHTS = {
    "management_confidence_shift": 0.25,
    "laggard_signal":              0.20,
    "guidance_gap":                0.20,
    "dso_trend":                   0.12,
    "inventory_velocity":          0.10,
    "segment_mix_shift":           0.08,
    "analyst_target_gap":          0.05,
}

SIGNAL_ORDER = list(SIGNAL_WEIGHTS.keys())


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _bar(fraction: float, width: int = 30) -> str:
    filled = round(fraction * width)
    return "#" * filled + "." * (width - filled)


def _pct(v: float | None) -> str:
    if v is None:
        return "   n/a"
    return f"{v * 100:5.1f}%"


def _fmt(v: float | None, decimals: int = 4) -> str:
    if v is None:
        return " " * (decimals + 3) + "n/a"
    return f"{v:+.{decimals}f}"


def _stars(delta: float) -> str:
    a = abs(delta)
    if a >= 0.05:
        return "***"
    if a >= 0.02:
        return "** "
    if a >= 0.005:
        return "*  "
    return "   "


def _divider(char: str = "-", width: int = 70) -> str:
    return char * width


def _header(title: str, width: int = 70) -> str:
    return f"\n{'=' * width}\n  {title}\n{'=' * width}"


def build_report(
    run_id: str,
    ticker_universe: list[str],
    from_date: str,
    to_date: str,
    threshold: float,
    total_processed: int,
    metrics: dict,
) -> str:
    lines: list[str] = []
    W = 70

    lines.append(_header("FINTEL BACKTEST REPORT", W))
    lines.append(f"  Generated : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"  Run ID    : {run_id}")
    lines.append(f"  Universe  : {len(ticker_universe)} ticker(s)  [{from_date}  to  {to_date}]")
    lines.append(f"  Threshold : {threshold}")
    lines.append(f"  Events    : {total_processed}")
    lines.append(_divider("-", W))

    # -- Accuracy --
    da = metrics.get("directional_accuracy")
    rga = metrics.get("relative_gap_accuracy")
    total = metrics.get("total_predictions", total_processed)

    lines.append("\nDIRECTIONAL ACCURACY")
    correct = round(da * total) if da is not None else 0
    lines.append(f"  Overall      : {_pct(da)}  ({correct}/{total})")
    lines.append(f"  Relative Gap : {_pct(rga)}")

    # -- Precision --
    pb = metrics.get("precision_bull")
    pbe = metrics.get("precision_bear")
    lines.append("\nPRECISION")
    lines.append(f"  Bull (up)    : {_pct(pb)}")
    lines.append(f"  Bear (down)  : {_pct(pbe)}")

    # -- Hit rate by regime --
    hbr: dict = metrics.get("hit_rate_by_regime") or {}
    if hbr:
        lines.append("\nHIT RATE BY REGIME")
        for label, rate in sorted(hbr.items(), key=lambda x: -x[1]):
            bar = _bar(rate, 25)
            lines.append(f"  {label:<20s}  {_pct(rate)}  {bar}")

    # -- Signal attribution --
    sa: dict = metrics.get("signal_attribution") or {}
    if sa:
        lines.append("\nSIGNAL ATTRIBUTION")
        lines.append(f"  {'Signal':<30s}  {'Wt':>4}  {'Correct':>8}  {'Incorrect':>9}  {'Delta':>7}  Sig")
        lines.append("  " + _divider("-", W - 2))
        ordered = sorted(
            sa.items(),
            key=lambda kv: SIGNAL_ORDER.index(kv[0]) if kv[0] in SIGNAL_ORDER else 99,
        )
        for name, vals in ordered:
            cm = vals.get("correct_mean")
            im = vals.get("incorrect_mean")
            delta = (cm - im) if (cm is not None and im is not None) else None
            wt = SIGNAL_WEIGHTS.get(name, 0.0)
            sig = _stars(delta) if delta is not None else "   "
            if name == "laggard_signal" and cm == im:
                sig = "(no data)"
            lines.append(
                f"  {name:<30s}  {wt:.2f}  {_fmt(cm, 4):>8}  {_fmt(im, 4):>9}  "
                f"{_fmt(delta, 4) if delta is not None else '     n/a':>7}  {sig}"
            )

    # -- Threshold sensitivity --
    ts: dict = metrics.get("threshold_sensitivity") or {}
    if ts:
        lines.append("\nTHRESHOLD SENSITIVITY")
        lines.append(f"  {'Threshold':>10}  {'Accuracy':>8}  Bar")
        lines.append("  " + _divider("-", 45))
        for t_str in ["0.1", "0.2", "0.3", "0.4"]:
            v = ts.get(t_str)
            marker = " <-- current" if abs(float(t_str) - threshold) < 1e-6 else ""
            bar = _bar(v or 0, 20) if v is not None else ""
            lines.append(f"  {float(t_str):>10.1f}  {_pct(v):>8}  {bar}{marker}")

    lines.append("\n" + "=" * W)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command("run-backtest-report")
@click.option("--ticker", "tickers", multiple=True,
              help="Ticker to include (repeat for multiple)")
@click.option("--universe", default=None, type=click.Choice(["all"]),
              help="'all' = every ticker in data/transcripts/")
@click.option("--from", "from_date", required=True, help="Start date YYYY-MM-DD")
@click.option("--to", "to_date", required=True, help="End date YYYY-MM-DD")
@click.option("--threshold", default=0.1, show_default=True, type=float,
              help="Composite score cutoff for directional prediction")
@click.option("--benchmark", default="SPY", show_default=True)
@click.option("--run-id", default=None, help="Reuse an existing run (skip backtest, go straight to report)")
@click.option("--out", default=None, help="Write report to this file path in addition to stdout")
def cli(tickers, universe, from_date, to_date, threshold, benchmark, run_id, out):
    """Run end-to-end backtest and print a formatted evaluation report.

    \b
    Full universe:
      python scripts/run_backtest_report.py --universe all \\
          --from 2018-01-01 --to 2022-12-31 --threshold 0.1

    Smoke test (2 tickers):
      python scripts/run_backtest_report.py --ticker SYN001 --ticker SYN002 \\
          --from 2018-01-01 --to 2018-12-31

    Re-evaluate an existing run (no backtest re-run):
      python scripts/run_backtest_report.py --run-id <UUID> \\
          --from 2018-01-01 --to 2022-12-31 --universe all
    """
    asyncio.run(_main(tickers, universe, from_date, to_date, threshold, benchmark, run_id, out))


async def _main(tickers, universe, from_date, to_date, threshold, benchmark, run_id, out):
    from src.agents.backtest_agent import run_backtest
    from eval.evaluators.backtest_eval import evaluate_backtest

    # Resolve ticker universe
    if universe == "all":
        ticker_universe = (
            [d.name for d in sorted(DATA_DIR.iterdir()) if d.is_dir()]
            if DATA_DIR.exists() else []
        )
    elif tickers:
        ticker_universe = [t.upper() for t in tickers]
    else:
        click.secho("Provide --ticker or --universe.", fg="red", err=True)
        sys.exit(1)

    # Run backtest (or reuse existing run_id)
    if run_id:
        click.echo(f"Skipping backtest — re-evaluating run {run_id} ...")
        total_processed = 0
    else:
        click.echo(
            f"Running backtest: {len(ticker_universe)} tickers  "
            f"[{from_date} to {to_date}]  threshold={threshold} ..."
        )
        result = await run_backtest(
            ticker_universe=ticker_universe,
            from_date=f"{from_date}T00:00:00Z",
            to_date=f"{to_date}T23:59:59Z",
            sentiment_threshold=threshold,
            benchmark=benchmark,
            run_id=run_id,
        )
        if result.get("error"):
            click.secho(f"Backtest error: {result['error']}", fg="red", err=True)
            sys.exit(1)

        run_id = result["run_id"]
        total_processed = result.get("total_processed", 0)
        click.echo(f"Backtest complete — run {run_id} ({total_processed} events)")

        # Persist run_id for convenience
        (Path(__file__).parent.parent / ".last_run_id").write_text(run_id)

    # Evaluate
    click.echo("Evaluating ...")
    metrics = await evaluate_backtest(run_id)
    total_processed = total_processed or metrics.get("total_predictions", 0)

    # Build report
    report = build_report(
        run_id=run_id,
        ticker_universe=ticker_universe,
        from_date=from_date,
        to_date=to_date,
        threshold=threshold,
        total_processed=total_processed,
        metrics=metrics,
    )

    click.echo(report)

    if out:
        Path(out).write_text(report, encoding="utf-8")
        click.secho(f"\nReport written to {out}", fg="green")


if __name__ == "__main__":
    cli()
