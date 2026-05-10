"""CLI entry point for Layer 4 evaluation.

Usage:
    python -m eval.runner backtest --run-id UUID
    python -m eval.runner backtest --run-id UUID --stratify-by regime
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import click


@click.group()
def cli():
    """Fintel evaluation framework — Layer 4 backtest metrics."""


@cli.command()
@click.option("--run-id", required=True, help="UUID of a completed backtest_run")
@click.option(
    "--stratify-by",
    default=None,
    type=click.Choice(["regime"]),
    help="Stratify output by regime (default: full summary only)",
)
def backtest(run_id: str, stratify_by: str | None):
    """Compute and print Layer 4 metrics for a completed backtest run."""
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

    click.echo(json.dumps(output, indent=2))


if __name__ == "__main__":
    cli()
