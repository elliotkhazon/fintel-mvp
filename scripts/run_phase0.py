#!/usr/bin/env python3
"""Phase 0 build runner — Synthetic data generation, Tiers 1-4.

Executes each tier in order: generate → functional test gate → reset → next tier.
Stops immediately if any step fails.

Usage (from project root):
    python scripts/run_phase0.py              # all tiers
    python scripts/run_phase0.py --start 2   # resume from Tier 2
    python scripts/run_phase0.py --dry-run   # print commands without running

Tier 5 (full 500-ticker dataset) is printed at the end but NOT run automatically.
It is a one-time permanent operation — run manually after Tier 4 passes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from shutil import rmtree

PROJECT_ROOT = Path(__file__).parent.parent
TRANSCRIPTS_DIR = PROJECT_ROOT / "data" / "transcripts"

# Ensure project root is on sys.path so in-process `import src.*` works.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ─── Helpers ──────────────────────────────────────────────────────────────────

_dry_run = False


def banner(text: str, char: str = "=") -> None:
    line = char * 70
    print(f"\n{line}\n  {text}\n{line}\n")


def step(text: str) -> None:
    print(f"  >> {text}")


def run(description: str, *args: str) -> None:
    step(description)
    cmd = [str(a) for a in args]
    print(f"     {' '.join(cmd)}")
    if _dry_run:
        return
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print(f"\n  [FAILED] {description} (exit {result.returncode})")
        print("  Fix the failure above before proceeding to the next tier.")
        sys.exit(result.returncode)


def _reset_db_via_sdk() -> None:
    """Drop and re-create the earnings_model database using the Python SurrealDB SDK."""
    import asyncio as _asyncio

    async def _drop():
        import src.db.connection as _conn
        await _conn.close_db()
        _conn._db = None
        from surrealdb import AsyncSurreal
        import os
        ns = os.getenv("SURREAL_NS", "fintel")
        db_name = os.getenv("SURREAL_DB", "earnings_model")
        url = os.getenv("SURREAL_URL", "ws://localhost:30800/rpc")
        db = AsyncSurreal(url)
        await db.connect()
        await db.signin({
            "username": os.getenv("SURREAL_USER", "root"),
            "password": os.getenv("SURREAL_PASS", "root"),
        })
        # Set namespace via SurrealQL then remove the database in one batch.
        # Avoids db.use(ns, None) which some SDK versions reject.
        result = await db.query(
            f"USE NS {ns}; REMOVE DATABASE IF EXISTS {db_name}"
        )
        print(f"     REMOVE result: {result}")
        await db.close()

    try:
        _asyncio.run(_drop())
        print("     SurrealDB earnings_model database removed.")
    except Exception as exc:
        print(f"     [ERROR] DB reset failed: {exc}")
        print("     Cannot continue — fix the connection before retrying.")
        sys.exit(1)

    # Re-apply schema so the DB is ready for the next tier.
    result = subprocess.run(
        [sys.executable, "-m", "src.db.init_schema"],
        cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        print("  [FAILED] Could not re-apply schema after DB reset.")
        sys.exit(result.returncode)
    print("     Schema re-applied.")


def reset_data_and_db() -> None:
    step("Wiping data/transcripts/ ...")
    if _dry_run:
        print(f"     [DRY RUN] rmtree({TRANSCRIPTS_DIR})")
    elif TRANSCRIPTS_DIR.exists():
        rmtree(TRANSCRIPTS_DIR)
        print(f"     Removed {TRANSCRIPTS_DIR}")
    else:
        print("     data/transcripts/ not found — nothing to wipe.")

    step("Resetting SurrealDB earnings_model database ...")
    if _dry_run:
        print("     [DRY RUN] REMOVE DATABASE earnings_model (via Python SDK)")
    else:
        _reset_db_via_sdk()


# ─── Step 0.1 — Schema ────────────────────────────────────────────────────────

def step_schema() -> None:
    banner("STEP 0.1 — Apply schema extensions")
    run("Apply schema.surql to SurrealDB",
        sys.executable, "-m", "src.db.init_schema")
    run("Verify schema: all new tables exist",
        sys.executable, "-m", "pytest",
        "tests/functional/test_schema.py", "-v", "--tb=short")
    print("  [OK] Schema verified.")


# ─── Tier 1 — Smoke ───────────────────────────────────────────────────────────

def tier1(reset_after: bool) -> None:
    banner("TIER 1 — SMOKE  (1 ticker × 1 year = 4 records)")
    run("Generate: 1 ticker × 1 year starting 2020",
        sys.executable, "scripts/datamanager.py",
        "generate-synthetic", "--tickers", "1", "--years", "1", "--start-year", "2020")
    run("Gate: smoke tests",
        sys.executable, "-m", "pytest",
        "tests/functional/test_schema.py",
        "tests/functional/test_synthetic_gen.py",
        "-k", "smoke", "-v", "--tb=short")
    print("  [PASS] Tier 1 gate cleared.")
    if reset_after:
        reset_data_and_db()


# ─── Tier 2 — NLP ─────────────────────────────────────────────────────────────

def tier2(reset_after: bool) -> None:
    banner("TIER 2 — NLP  (5 tickers × 2 years, --use-llm)")
    print("  NOTE: Requires model packages:")
    print("    pip install transformers spacy sentence-transformers")
    print("    python -m spacy download en_core_web_sm\n")
    run("Generate: 5 tickers × 2 years starting 2020 with LLM guidance text",
        sys.executable, "scripts/datamanager.py",
        "generate-synthetic", "--tickers", "5", "--years", "2",
        "--start-year", "2020", "--use-llm")
    run("Gate: FinBERT + spaCy + SBERT tests",
        sys.executable, "-m", "pytest",
        "tests/functional/test_finbert_extractor.py",
        "tests/functional/test_confidence_scorer.py",
        "tests/functional/test_analyst_pressure.py",
        "-v", "--tb=short")
    run("Gate: smoke tests still pass on Tier 2 data",
        sys.executable, "-m", "pytest",
        "tests/functional/test_synthetic_gen.py",
        "-k", "smoke", "-v", "--tb=short")
    print("  [PASS] Tier 2 gate cleared.")
    if reset_after:
        reset_data_and_db()


# ─── Tier 3 — Regime ──────────────────────────────────────────────────────────

def tier3(reset_after: bool) -> None:
    banner("TIER 3 — REGIME  (20 tickers × 4 years, all 4 regime labels)")
    run("Generate: 20 tickers × 4 years starting 2016",
        sys.executable, "scripts/datamanager.py",
        "generate-synthetic", "--tickers", "20", "--years", "4", "--start-year", "2016")
    run("Gate: regime classifier tests",
        sys.executable, "-m", "pytest",
        "tests/functional/test_regime_classifier.py", "-v", "--tb=short")
    run("Gate: occurred_during edge coverage tests",
        sys.executable, "-m", "pytest",
        "tests/functional/test_regime_edges.py", "-v", "--tb=short")
    print("  [PASS] Tier 3 gate cleared.")
    if reset_after:
        reset_data_and_db()


# ─── Tier 4 — Integration ─────────────────────────────────────────────────────

def tier4() -> None:
    banner("TIER 4 — INTEGRATION  (50 tickers × 5 years, full suite)")
    run("Generate: 50 tickers × 5 years starting 2018",
        sys.executable, "scripts/datamanager.py",
        "generate-synthetic", "--tickers", "50", "--years", "5", "--start-year", "2018")
    run("Gate: full functional test suite",
        sys.executable, "-m", "pytest",
        "tests/functional/", "-v", "--tb=short")
    print("  [PASS] Tier 4 gate cleared.")


# ─── Fit HMM on synthetic macro data ─────────────────────────────────────────

def step_fit_hmm_synthetic() -> None:
    banner("STEP — Fit HMM on synthetic macro data (2010–2026)")
    run("Fit 4-state GaussianHMM → models/hmm_regime.pkl",
        sys.executable, "scripts/datamanager.py",
        "fit-hmm", "--synthetic",
        "--from", "2010-01-01", "--to", "2026-12-31")
    run("Gate: HMM loads, classifies all known year/quarter pairs correctly",
        sys.executable, "-m", "pytest",
        "tests/functional/test_regime_classifier.py", "-v", "--tb=short")
    print("  [PASS] HMM pipeline validated — models/hmm_regime.pkl ready.")


# ─── Post-run instructions ────────────────────────────────────────────────────

def print_next_steps() -> None:
    banner("PHASE 0 COMPLETE — All tiers + synthetic HMM passed.", char="-")
    print("  Phase 1 — replace synthetic HMM with real macro data:")
    print("    python scripts/datamanager.py fit-hmm --from 2010-01-01 --to 2026-12-31")
    print("    (requires FMP macro data; see plan.final.md §4)")
    print()
    print("  Tier 5 — ONE-TIME permanent dataset (run manually after Phase 1 passes):")
    print("    python scripts/run_phase0.py  # resets DB then generates 500 tickers")
    print("    python scripts/datamanager.py generate-synthetic \\")
    print("        --tickers 500 --years 10 --start-year 2016")
    print("    python -m pytest tests/functional/test_synthetic_gen.py -v")
    print()


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    global _dry_run

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=int, default=0, metavar="TIER",
                        choices=range(0, 6),
                        help="Start from this step (0=schema, 1-4=tiers, 5=fit-hmm). Default: 0")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands without executing them")
    args = parser.parse_args()

    _dry_run = args.dry_run
    start = args.start

    if _dry_run:
        banner("DRY RUN — commands will be printed but not executed", char="-")

    if start > 0:
        # Resuming mid-run: wipe any stale data so the gate tests start clean.
        banner(f"Resetting DB before resuming at Tier {start}", char="-")
        reset_data_and_db()

    if start == 0:
        step_schema()

    if start <= 1:
        tier1(reset_after=(start < 2))
    if start <= 2:
        tier2(reset_after=(start < 3))
    if start <= 3:
        tier3(reset_after=(start < 4))
    if start <= 4:
        tier4()
    if start <= 5:
        step_fit_hmm_synthetic()

    print_next_steps()


if __name__ == "__main__":
    main()
