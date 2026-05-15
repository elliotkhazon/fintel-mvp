# Fintel MVP — Earnings Intelligence Platform

Fintel surfaces what the market is missing: connecting a supplier's "record shipments" to a
customer's "flat analyst estimates" before the earnings call happens.

The platform ingests earnings call transcripts, extracts relational signals via a graph database,
and produces beat/miss probability reports backed by seven quantitative signals and an LLM
synthesis step. It is a full local MVP (FastAPI + SurrealDB + LangGraph), with AWS
infrastructure provisioned for cloud deployment.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Tech Stack](#tech-stack)
- [Directory Structure](#directory-structure)
- [Core Components](#core-components)
  - [API Layer](#api-layer)
  - [Agent Pipeline](#agent-pipeline)
  - [Signal Engine](#signal-engine)
  - [Graph Database](#graph-database)
  - [Market Regime Classifier](#market-regime-classifier)
  - [Backtesting](#backtesting)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [CLI Reference](#cli-reference)
- [Testing](#testing)
- [Infrastructure & Deployment](#infrastructure--deployment)
- [Research](#research)

---

## How It Works

```
Earnings Transcripts (JSON)
       │
       ▼
  Extraction Agent  ─────────────────────────────────────────┐
  (LangGraph + Gemini)                                        │
  • Entity extraction (companies, metrics, events)            │
  • Sentiment scoring via FinBERT                             │
  • Guidance gap computation                                  │
       │                                                      │
       ▼                                                      ▼
  SurrealDB Graph  ◄──── FMP Mock API (key metrics,    Supply Chain
  earnings_model        segments, price targets)        Config JSON
  • company nodes                                       (suppliers /
  • transcript_doc                                       customers /
  • expressed_sentiment edges                           competitors)
  • guidance_entry
  • key_metric_snapshot
  • revenue_segment
  • analyst_target
       │
       ▼
  Signal Agent ── 7 weighted signals ──► composite_score → beat_probability
       │
       ▼
  Prediction Agent ── Gemini LLM synthesis ──► PredictionReport
       │
       ▼
  POST /v1/predictions/{ticker}  →  JSON report
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI + Uvicorn (async) |
| Graph database | SurrealDB 2.0 (WebSocket async) |
| Agent orchestration | LangGraph + LangChain |
| LLM | Google Gemini 2.5 Flash |
| NLP | FinBERT (sentiment), spaCy (NER), sentence-transformers (embeddings) |
| Statistical models | hmmlearn (Hidden Markov Model — regime classification) |
| Cloud | AWS (VPC, S3, ECR, Secrets Manager, IAM) |
| Infrastructure-as-code | Terraform 1.8 |
| CI/CD | GitHub Actions + OIDC (no static keys) |
| Container runtime | k3s (cloud), Docker Desktop (local) |
| Python | 3.11 |

---

## Directory Structure

```
fintel/mvp/
├── .github/workflows/        # CI/CD: ci.yml, deploy-staging.yml, deploy-prod.yml
├── config/
│   ├── signal_weights.json   # Weight per signal (sum = 1.0)
│   └── supply_chains.json    # Supplier / customer / competitor edges (seed data)
├── data/
│   └── transcripts/          # Earnings call JSON, organized by ticker
├── docs/
│   ├── tech_design.v1.md     # GraphRAG architecture and data model
│   ├── signals.md            # Signal computation formulas and interpretation
│   ├── backtesting_pipeline/ # Multi-phase backtesting implementation specs
│   ├── cloud_deployment/     # Phase 0–4 cloud transition plans
│   └── evaluation_framework/ # Backtesting methodology and validation gates
├── eval/                     # Backtesting evaluation framework
├── infra/
│   ├── main.tf               # Root Terraform config (networking + storage + IAM)
│   ├── variables.tf
│   ├── outputs.tf
│   ├── backend.tf            # Remote state (S3 + DynamoDB lock)
│   ├── modules/
│   │   ├── networking/       # VPC, private subnets, security groups, VPC endpoints
│   │   ├── storage/          # S3 buckets, ECR, Secrets Manager
│   │   └── iam/              # GitHub OIDC provider, CI roles
│   ├── deploy.ps1 / deploy.sh
│   ├── destroy.ps1 / destroy.sh
│   ├── recreate.ps1 / recreate.sh
│   └── RUNBOOK.md            # Step-by-step deployment guide
├── models/
│   └── hmm_regime.pkl        # Trained HMM regime classifier
├── notebook/
│   └── backtest.ipynb        # Backtesting research and analysis
├── prompts/                  # Agentic workflow prompts and design docs
├── scripts/
│   ├── datamanager.py        # CLI: generate / ingest / fetch / list transcripts
│   ├── run_phase0.py         # Phase 0 smoke runner
│   ├── generate_synthetic_backtest.py
│   └── run_backtest_report.py
├── src/
│   ├── api/
│   │   ├── main.py           # FastAPI app entry point
│   │   └── graph.py          # Graph + prediction router
│   ├── agents/
│   │   ├── extraction_agent.py  # Transcript → graph (LangGraph)
│   │   ├── signal_agent.py      # 7-signal scoring
│   │   ├── prediction_agent.py  # End-to-end prediction + LLM report
│   │   ├── backtest_agent.py    # Historical backtesting pipeline
│   │   └── transcript_agent.py  # Transcript retrieval and normalization
│   ├── db/
│   │   ├── connection.py        # Async SurrealDB singleton client
│   │   ├── schema.surql         # DDL — all tables and edges
│   │   ├── init_schema.py       # Applies schema.surql idempotently
│   │   ├── normalizer.py        # Entity dedup and upsert helpers
│   │   ├── graph_queries.py     # Multi-hop SurrealQL traversal functions
│   │   └── relationship_seeder.py  # Seeds supply chain edges from config/
│   └── models/
│       ├── graph_models.py      # SignalScore, SignalBundle, PredictionReport
│       ├── regime_classifier.py # HMM + deterministic regime classification
│       ├── finbert_extractor.py # FinBERT sentiment extraction
│       ├── analyst_pressure.py  # Analyst vs. guidance gap computation
│       ├── confidence_scorer.py # Beat probability calibration
│       └── transcript.py        # Transcript and TranscriptDateEntry (Pydantic)
├── tests/
│   ├── unit/                 # No AWS — validates IaC structure and core logic
│   ├── functional/           # Local DB required
│   ├── integration/          # Live AWS stack required (FINTEL_ENV=staging)
│   └── regression/           # Signal drift and output stability
├── main.py                   # Uvicorn entry point
├── requirements.txt
└── .env.example
```

---

## Core Components

### API Layer

The FastAPI server (`src/api/main.py`) exposes two categories of routes:

**Transcript mock endpoints** (FMP-compatible — used during local MVP before real FMP API
integration):

| Method | Path | Description |
|---|---|---|
| `GET` | `/stable/search-transcripts` | Full-text search across loaded transcripts |
| `GET` | `/v3/earning_call_transcript/{symbol}` | Fetch transcript by symbol / quarter / year |
| `GET` | `/v4/transcript-dates` | Available transcript dates |
| `GET` | `/health` | Health check |

**FMP data mock endpoints** (static fixtures, replaced by real FMP calls in Phase 1):

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v3/key-metrics/{symbol}` | DSO, inventory turnover, gross margin |
| `GET` | `/api/v3/revenue-product-segmentation/{symbol}` | Product / segment revenue breakdown |
| `GET` | `/api/v3/price-target-consensus/{symbol}` | Analyst consensus, high, low, median targets |

**Graph and prediction endpoints** (`src/api/graph.py`):

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/graph/ingest/{symbol}` | Run extraction agent on unprocessed transcripts |
| `GET` | `/v1/graph/company/{ticker}` | Company node + first-degree edges |
| `GET` | `/v1/graph/signals/{ticker}` | Raw 7-signal bundle (no LLM synthesis) |
| `POST` | `/v1/predictions/{ticker}` | Full prediction report (`{quarter, year}` body) |

---

### Agent Pipeline

All agents are implemented as LangGraph state machines. They share a common pattern: parallel
data-fetch branches fan out, results converge into a scoring or persistence node.

#### Extraction Agent (`src/agents/extraction_agent.py`)

Triggered per transcript. Runs four branches in parallel after loading transcript content.

```
load_transcript
    │ (parallel fan-out)
    ├── extract_entities       ← Gemini LLM (companies, metrics, events, guidance)
    ├── fetch_key_metrics      ← /api/v3/key-metrics/{symbol}
    ├── fetch_segments         ← /api/v3/revenue-product-segmentation/{symbol}
    └── fetch_price_targets    ← /api/v3/price-target-consensus/{symbol}
          │ (fan-in)
    normalize_entities
    ├── persist_graph          ← company nodes + sentiment + relationship edges
    └── persist_fundamentals   ← key_metric_snapshot + revenue_segment + analyst_target
          │
    mark_processed
```

The LLM extraction step produces structured JSON validated against this schema:

```json
{
  "companies_mentioned": [
    {"ticker": "AMD", "name": "Advanced Micro Devices", "relationship": "competitor"}
  ],
  "metrics": [
    {"name": "Gross Margin", "value_mentioned": "73%", "sentiment_score": 0.8,
     "context": "<exact quote>", "section": "prepared"}
  ],
  "events": [
    {"name": "Data Center Demand Surge", "type": "macro", "relevance": 0.9}
  ],
  "guidance": {
    "metric": "Revenue", "company_guide": 28.0, "analyst_est": 26.8, "unit": "billion_usd"
  }
}
```

#### Prediction Agent (`src/agents/prediction_agent.py`)

Triggered by `POST /v1/predictions/{ticker}`. Five data-fetch branches run concurrently via
`asyncio.gather()`, then converge into signal scoring and LLM report generation.

```
(parallel fan-out)
├── fetch_hop1    ← sentiment per metric (last 4 quarters)
├── fetch_hop2    ← competitor sentiment via competes_with edges
├── fetch_hop3    ← supplier demand signals via supplied_by edges
├── fetch_fundamentals  ← DSO history, inventory turnover, segment mix
└── fetch_targets       ← analyst consensus price targets
      │ (fan-in)
score_signals     ← 7-signal composite bundle
      │
generate_report   ← Gemini LLM synthesis
      │
PredictionReport
```

#### Signal Agent (`src/agents/signal_agent.py`)

Converts raw graph data into `SignalBundle`. Used standalone by
`GET /v1/graph/signals/{ticker}` and internally by the Prediction Agent.

#### Backtest Agent (`src/agents/backtest_agent.py`)

Runs historical simulations over a ticker universe and date range:

```
resolve_events
    │
process_all_events   ← per event: classify_regime → load_price_gaps → compute_signals → compare_outcome
    │
aggregate_metrics    ← directional accuracy, hit rate by regime
    │
persist_metrics      ← writes BacktestRun to SurrealDB
```

---

### Signal Engine

Seven signals are computed per (ticker, quarter, year). All scores are normalized to **[-1.0, 1.0]**
and a weighted composite score determines beat probability.

| # | Signal | Weight | What It Measures |
|---|---|---|---|
| 1 | `management_confidence_shift` | 0.25 | QA tone delta vs. prior 4-quarter average — rising confidence precedes beats |
| 2 | `laggard_signal` | 0.20 | Whether sector peers are showing positive sentiment — sector tailwinds lift laggards |
| 3 | `guidance_gap` | 0.20 | Conservative guidance relative to analyst consensus — room to beat |
| 4 | `dso_trend` | 0.12 | QoQ change in Days Sales Outstanding — falling DSO = faster cash collection |
| 5 | `inventory_velocity` | 0.10 | QoQ % change in inventory turnover — acceleration = demand pull-through |
| 6 | `segment_mix_shift` | 0.08 | Top segment growth vs. blended revenue — mix tailwind when flagship leads |
| 7 | `analyst_target_gap` | 0.05 | Analyst consensus target vs. company guidance — sell-side optimism signal |

**Composite score and beat probability:**

```
composite = clamp( Σ signal.score × weight )

composite > 0.3   →  beat_probability = "High"
composite > 0.05  →  beat_probability = "Medium"
composite ≤ 0.05  →  beat_probability = "Low"
```

Signal weights are runtime-configurable via `config/signal_weights.json`.

For full formula derivations and null-handling rules, see [docs/signals.md](docs/signals.md).

---

### Graph Database

SurrealDB (`earnings_model` database, `fintel` namespace) stores all graph nodes and edges.
Schema is defined in `src/db/schema.surql` and applied idempotently by `src/db/init_schema.py`
on startup.

**Node tables:**

| Table | Purpose |
|---|---|
| `company` | Canonical company node (`ticker`, `name`, `sector`, `industry`) |
| `metric` | Named financial/operational metric (e.g., "Gross Margin", "DSO") |
| `event` | Macro, competitive, or regulatory event |
| `transcript_doc` | Earnings call document linked to a company + period |
| `key_metric_snapshot` | Per-period DSO, inventory turnover, gross margin (from FMP) |
| `revenue_segment` | Per-period segment revenue breakdown (from FMP) |
| `analyst_target` | Analyst consensus price target (from FMP) |
| `guidance_entry` | Company guidance vs. analyst estimate, plus realized actual |

**Relationship edges:**

| Edge | Direction | Key Fields |
|---|---|---|
| `expressed_sentiment` | `transcript_doc → metric` | `score [-1,1]`, `context` (quote), `section` |
| `lead_indicator_for` | `company → company` | `metric`, `lag_quarters` |
| `competes_with` | `company → company` | `overlap` (end_market / product) |
| `supplied_by` | `company → company` | `materiality` (primary / secondary) |
| `sold_to` | `company → company` | `materiality` |
| `reported_in` | `event → transcript_doc` | `relevance [0,1]` |

Supply chain edges (`supplied_by`, `sold_to`, `competes_with`) are seeded from
`config/supply_chains.json`, which covers major tech tickers: NVDA, AMD, AAPL, MSFT, GOOGL,
TSM, and others.

---

### Market Regime Classifier

`src/models/regime_classifier.py` classifies each historical quarter into one of four market
regimes used to segment backtest results:

| Regime | Description |
|---|---|
| `GrowthExpansion` | Stable growth, low volatility |
| `BlackSwan` | Acute market stress (e.g., 2020 COVID) |
| `HighInflation` | Elevated CPI, Fed tightening cycle |
| `AIExpansion` | AI-driven growth cycle (2023–present) |

**Phase 0 (active):** Deterministic year-based lookup — no model needed, fully reproducible
for backtesting.

**Phase 1 (trained model available):** HMM classifier using VIX, Fed Funds rate, and CPI
features. The trained model is stored at `models/hmm_regime.pkl`.

---

### Backtesting

The backtesting framework (`src/agents/backtest_agent.py`, `eval/`) validates signal quality
over historical earnings events:

1. For each (ticker, quarter, year) event in the universe, classify the market regime.
2. Compute the 7-signal bundle using only data available **before** that quarter (no look-ahead).
3. Compare the predicted `beat_probability` against the realized outcome.
4. Aggregate directional accuracy and hit rate broken out by market regime.

Results are persisted to SurrealDB (`BacktestRun`) and can be explored interactively in
[notebook/backtest.ipynb](notebook/backtest.ipynb).

---

## Getting Started

### Prerequisites

| Tool | Version |
|---|---|
| Python | 3.11 |
| Docker Desktop | latest |
| SurrealDB | v2.0+ |

### Local setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and populate environment variables
cp .env.example .env
# Edit .env — set GOOGLE_API_KEY, SURREAL_* credentials

# 3. Start SurrealDB (Docker)
docker run --rm -p 8000:8000 surrealdb/surrealdb:latest \
  start --log trace --user root --pass root memory

# 4. Apply the schema
python -m src.db.init_schema

# 5. Seed supply chain relationships
python -m src.db.relationship_seeder

# 6. Start the API server
uvicorn main:app --reload --port 8000
```

### Generate and ingest synthetic transcripts

```bash
# Generate synthetic transcripts for NVDA Q1–Q4 2023
python scripts/datamanager.py bulk-generate --symbol NVDA --start-year 2023 --end-year 2023

# Ingest all unprocessed transcripts into the graph
python scripts/datamanager.py ingest --all

# Pull FMP mock fundamentals for NVDA
python scripts/datamanager.py fetch-fundamentals --symbol NVDA
```

### Run a prediction

```bash
curl -X POST http://localhost:8000/v1/predictions/NVDA \
  -H "Content-Type: application/json" \
  -d '{"quarter": 1, "year": 2024}'
```

---

## Configuration

### Environment Variables

```bash
# Google Gemini (LLM extraction + report generation)
GOOGLE_API_KEY=your-gemini-api-key-here
GEMINI_MODEL=gemini-2.5-flash

# SurrealDB
SURREAL_HTTP_ENDPOINT=http://localhost:8000
SURREAL_URL=ws://localhost:8000/rpc
SURREAL_USER=root
SURREAL_PASS=root
SURREAL_NS=fintel
SURREAL_DB=earnings_model
```

### Signal Weights (`config/signal_weights.json`)

Weights must sum to 1.0. Modify to tune the composite score toward signals with stronger
predictive performance in your universe.

```json
{
  "management_confidence_shift": 0.25,
  "laggard_signal":              0.20,
  "guidance_gap":                0.20,
  "dso_trend":                   0.12,
  "inventory_velocity":          0.10,
  "segment_mix_shift":           0.08,
  "analyst_target_gap":          0.05
}
```

### Supply Chain Graph (`config/supply_chains.json`)

Defines directed supply chain and competitive relationships for the seed company universe.
Extend this file to add new tickers before running `relationship_seeder.py`.

```json
{
  "NVDA": {
    "suppliers":   ["TSM", "ASML"],
    "customers":   ["MSFT", "GOOGL", "META"],
    "competitors": ["AMD", "INTC"]
  }
}
```

---

## API Reference

### Start the server

```bash
uvicorn main:app --reload --port 8000
# Interactive docs: http://localhost:8000/docs
```

### Key endpoints

**Generate a prediction report:**
```bash
POST /v1/predictions/{ticker}
Body: {"quarter": 1, "year": 2024}
```

**Get raw signals (no LLM):**
```bash
GET /v1/graph/signals/{ticker}?quarter=1&year=2024
```

**Ingest unprocessed transcripts:**
```bash
POST /v1/graph/ingest/{ticker}
```

**Fetch company graph node:**
```bash
GET /v1/graph/company/{ticker}
```

---

## CLI Reference

`scripts/datamanager.py` is the primary data management CLI.

```bash
# Transcript generation
python scripts/datamanager.py generate --symbol NVDA --quarter 1 --year 2024
python scripts/datamanager.py bulk-generate --symbol NVDA --start-year 2022 --end-year 2024

# Graph ingestion
python scripts/datamanager.py ingest --symbol NVDA
python scripts/datamanager.py ingest --all

# FMP data fetch (writes to graph)
python scripts/datamanager.py fetch-metrics --symbol NVDA --period quarter
python scripts/datamanager.py fetch-segments --symbol NVDA
python scripts/datamanager.py fetch-price-targets --symbol NVDA
python scripts/datamanager.py fetch-fundamentals --symbol NVDA   # runs all three

# Inspect transcripts
python scripts/datamanager.py list
python scripts/datamanager.py list --symbol NVDA
python scripts/datamanager.py show NVDA 1 2024
python scripts/datamanager.py delete NVDA 1 2024
```

---

## Testing

The test suite is divided into four tiers. Tests are gated per environment.

| Tier | Path | Requirements | When it runs |
|---|---|---|---|
| Unit | `tests/unit/` | No AWS, no DB | PR gate (ci.yml) |
| Functional | `tests/functional/` | Local SurrealDB | Local only |
| Integration | `tests/integration/` | Deployed AWS stack (`FINTEL_ENV=staging`) | Post-deploy (deploy-staging.yml) |
| Regression | `tests/regression/` | Deployed stack | Post-deploy (deploy-staging.yml) |

```bash
# Unit tests (no dependencies)
pytest tests/unit/ -v

# Unit + functional (requires local SurrealDB)
pytest tests/unit/ tests/functional/ -v --tb=short

# Integration tests (requires deployed staging stack)
export FINTEL_ENV=staging
export AWS_PROFILE=fintel-staging
pytest tests/integration/ -v

# Smoke tests only (prod deploy gate)
pytest tests/integration/ -m smoke -v
```

---

## Infrastructure & Deployment

AWS infrastructure is provisioned by Terraform (Phase 0). All resources are tagged by
environment (`staging` / `prod`).

### Provisioned resources (Phase 0)

| Module | Resources |
|---|---|
| **Networking** | VPC (10.0.0.0/16), 2 private subnets, security groups, VPC endpoints (S3, ECR, Secrets Manager, CloudWatch) |
| **Storage** | S3 buckets (transcripts, artifacts, glue scripts), ECR repository, Secrets Manager secrets |
| **IAM** | GitHub OIDC provider, CI roles for staging and prod (no static keys) |

### Deploy to staging

```bash
# Bash
./infra/deploy.sh staging

# PowerShell
.\infra\deploy.ps1 -Env staging
```

### Deploy to staging and push secrets from .env

```powershell
.\infra\deploy.ps1 -Env staging -DeploySecrets
```

### Bootstrap remote state (first time only per environment)

```powershell
$Env = "staging"

aws s3api create-bucket --bucket "fintel-tf-state-$Env" --region us-east-1

aws dynamodb create-table `
  --table-name "fintel-tf-locks-$Env" `
  --attribute-definitions AttributeName=LockID,AttributeType=S `
  --key-schema AttributeName=LockID,KeyType=HASH `
  --billing-mode PAY_PER_REQUEST `
  --region us-east-1
```

### Enable GitHub Actions

Workflows are disabled by default. Enable via **GitHub → Settings → Variables**:
`FINTEL_WORKFLOWS_ENABLED = true`

### Teardown

```powershell
.\infra\destroy.ps1 -Env staging    # destroys compute/network, preserves S3 data
.\infra\recreate.ps1 -Env staging   # full rebuild from scratch
```

### CI/CD pipeline

| Workflow | Trigger | Actions |
|---|---|---|
| `ci.yml` | PR → main | Terraform fmt/validate/plan, unit tests |
| `deploy-staging.yml` | Push → main | Terraform apply staging, integration + regression tests |
| `deploy-prod.yml` | Tag `v*` or manual | Terraform plan (review) → manual approval → apply prod → smoke tests |

For full deployment instructions, prerequisites, and troubleshooting, see
[infra/RUNBOOK.md](infra/RUNBOOK.md).

---

## Research

The [notebook/](notebook/) folder contains research and exploratory analysis. For a
comprehensive overview of how the backtesting framework works and historical signal
performance, see [notebook/backtest.ipynb](notebook/backtest.ipynb).
