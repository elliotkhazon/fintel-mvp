-- earnings_model schema — SurrealDB v2.x required for UPSERT statement.
-- This file is idempotent: safe to re-run via init_schema.py.

-- ---------------------------------------------------------------------------
-- Nodes
-- ---------------------------------------------------------------------------

DEFINE TABLE IF NOT EXISTS company SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS ticker      ON company TYPE string;
DEFINE FIELD IF NOT EXISTS name        ON company TYPE string;
DEFINE FIELD IF NOT EXISTS sector      ON company TYPE option<string>;
DEFINE FIELD IF NOT EXISTS industry    ON company TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_ticker  ON company FIELDS ticker UNIQUE;

DEFINE TABLE IF NOT EXISTS metric SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS name        ON metric TYPE string;
DEFINE FIELD IF NOT EXISTS category    ON metric TYPE string;
DEFINE INDEX IF NOT EXISTS idx_metric  ON metric FIELDS name UNIQUE;

DEFINE TABLE IF NOT EXISTS event SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS name        ON event TYPE string;
DEFINE FIELD IF NOT EXISTS event_type  ON event TYPE string;

DEFINE TABLE IF NOT EXISTS transcript_doc SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS company     ON transcript_doc TYPE record<company>;
DEFINE FIELD IF NOT EXISTS quarter     ON transcript_doc TYPE int;
DEFINE FIELD IF NOT EXISTS year        ON transcript_doc TYPE int;
DEFINE FIELD IF NOT EXISTS date        ON transcript_doc TYPE datetime;
DEFINE FIELD IF NOT EXISTS raw_path    ON transcript_doc TYPE string;
DEFINE FIELD IF NOT EXISTS processed   ON transcript_doc TYPE bool DEFAULT false;
DEFINE INDEX IF NOT EXISTS idx_trans   ON transcript_doc FIELDS company, quarter, year UNIQUE;

-- FMP /api/v3/key-metrics/{symbol}
DEFINE TABLE IF NOT EXISTS key_metric_snapshot SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS company             ON key_metric_snapshot TYPE record<company>;
DEFINE FIELD IF NOT EXISTS period              ON key_metric_snapshot TYPE string;
DEFINE FIELD IF NOT EXISTS dso                 ON key_metric_snapshot TYPE option<float>;
DEFINE FIELD IF NOT EXISTS inventory_turnover  ON key_metric_snapshot TYPE option<float>;
DEFINE FIELD IF NOT EXISTS revenue_per_share   ON key_metric_snapshot TYPE option<float>;
DEFINE FIELD IF NOT EXISTS gross_profit_margin ON key_metric_snapshot TYPE option<float>;
DEFINE FIELD IF NOT EXISTS fetched_at          ON key_metric_snapshot TYPE datetime;
DEFINE INDEX IF NOT EXISTS idx_km ON key_metric_snapshot FIELDS company, period UNIQUE;

-- FMP /api/v3/revenue-product-segmentation/{symbol}
DEFINE TABLE IF NOT EXISTS revenue_segment SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS company      ON revenue_segment TYPE record<company>;
DEFINE FIELD IF NOT EXISTS period       ON revenue_segment TYPE string;
DEFINE FIELD IF NOT EXISTS segment_name ON revenue_segment TYPE string;
DEFINE FIELD IF NOT EXISTS revenue      ON revenue_segment TYPE float;
DEFINE FIELD IF NOT EXISTS pct_of_total ON revenue_segment TYPE option<float>;
DEFINE FIELD IF NOT EXISTS fetched_at   ON revenue_segment TYPE datetime;
DEFINE INDEX IF NOT EXISTS idx_seg ON revenue_segment FIELDS company, period, segment_name UNIQUE;

-- FMP /api/v3/price-target-consensus/{symbol}
DEFINE TABLE IF NOT EXISTS analyst_target SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS company          ON analyst_target TYPE record<company>;
DEFINE FIELD IF NOT EXISTS target_high      ON analyst_target TYPE option<float>;
DEFINE FIELD IF NOT EXISTS target_low       ON analyst_target TYPE option<float>;
DEFINE FIELD IF NOT EXISTS target_consensus ON analyst_target TYPE option<float>;
DEFINE FIELD IF NOT EXISTS target_median    ON analyst_target TYPE option<float>;
DEFINE FIELD IF NOT EXISTS fetched_at       ON analyst_target TYPE datetime;
DEFINE INDEX IF NOT EXISTS idx_at ON analyst_target FIELDS company, fetched_at UNIQUE;

DEFINE TABLE IF NOT EXISTS guidance_entry SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS company       ON guidance_entry TYPE record<company>;
DEFINE FIELD IF NOT EXISTS metric        ON guidance_entry TYPE record<metric>;
DEFINE FIELD IF NOT EXISTS quarter       ON guidance_entry TYPE int;
DEFINE FIELD IF NOT EXISTS year          ON guidance_entry TYPE int;
DEFINE FIELD IF NOT EXISTS company_guide ON guidance_entry TYPE option<float>;
DEFINE FIELD IF NOT EXISTS analyst_est   ON guidance_entry TYPE option<float>;
DEFINE FIELD IF NOT EXISTS actual        ON guidance_entry TYPE option<float>;
DEFINE FIELD IF NOT EXISTS beat          ON guidance_entry TYPE option<bool>;

-- ---------------------------------------------------------------------------
-- Edges
-- ---------------------------------------------------------------------------

DEFINE TABLE IF NOT EXISTS expressed_sentiment SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS in      ON expressed_sentiment TYPE record<transcript_doc>;
DEFINE FIELD IF NOT EXISTS out     ON expressed_sentiment TYPE record<metric>;
DEFINE FIELD IF NOT EXISTS score   ON expressed_sentiment TYPE float;
DEFINE FIELD IF NOT EXISTS context ON expressed_sentiment TYPE string;
DEFINE FIELD IF NOT EXISTS section ON expressed_sentiment TYPE string;

DEFINE TABLE IF NOT EXISTS lead_indicator_for SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS in           ON lead_indicator_for TYPE record<company>;
DEFINE FIELD IF NOT EXISTS out          ON lead_indicator_for TYPE record<company>;
DEFINE FIELD IF NOT EXISTS metric       ON lead_indicator_for TYPE record<metric>;
DEFINE FIELD IF NOT EXISTS lag_quarters ON lead_indicator_for TYPE int DEFAULT 1;

DEFINE TABLE IF NOT EXISTS competes_with SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS in      ON competes_with TYPE record<company>;
DEFINE FIELD IF NOT EXISTS out     ON competes_with TYPE record<company>;
DEFINE FIELD IF NOT EXISTS overlap ON competes_with TYPE string;

DEFINE TABLE IF NOT EXISTS supplied_by SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS in          ON supplied_by TYPE record<company>;
DEFINE FIELD IF NOT EXISTS out         ON supplied_by TYPE record<company>;
DEFINE FIELD IF NOT EXISTS materiality ON supplied_by TYPE string;

DEFINE TABLE IF NOT EXISTS sold_to SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS in          ON sold_to TYPE record<company>;
DEFINE FIELD IF NOT EXISTS out         ON sold_to TYPE record<company>;
DEFINE FIELD IF NOT EXISTS materiality ON sold_to TYPE string;

DEFINE TABLE IF NOT EXISTS reported_in SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS in        ON reported_in TYPE record<event>;
DEFINE FIELD IF NOT EXISTS out       ON reported_in TYPE record<transcript_doc>;
DEFINE FIELD IF NOT EXISTS relevance ON reported_in TYPE float;

-- ---------------------------------------------------------------------------
-- Backtesting Pipeline Extensions (Phase 0)
-- ---------------------------------------------------------------------------

-- New fields on transcript_doc for NLP-derived scores
DEFINE FIELD IF NOT EXISTS confidence_score       ON transcript_doc TYPE option<float>;
DEFINE FIELD IF NOT EXISTS analyst_pressure_index ON transcript_doc TYPE option<float>;

-- Market regime node (one row per regime label; upserted idempotently)
DEFINE TABLE IF NOT EXISTS regime SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS label        ON regime TYPE string;
DEFINE FIELD IF NOT EXISTS start_date   ON regime TYPE datetime;
DEFINE FIELD IF NOT EXISTS end_date     ON regime TYPE datetime;
DEFINE FIELD IF NOT EXISTS hmm_state_id ON regime TYPE int;
DEFINE FIELD IF NOT EXISTS key_signals  ON regime TYPE array<string>;
DEFINE INDEX IF NOT EXISTS idx_regime   ON regime FIELDS label UNIQUE;

-- Price-gap outcome (one row per earnings event)
DEFINE TABLE IF NOT EXISTS price_gap_outcome SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS company           ON price_gap_outcome TYPE record<company>;
DEFINE FIELD IF NOT EXISTS transcript        ON price_gap_outcome TYPE record<transcript_doc>;
DEFINE FIELD IF NOT EXISTS regime            ON price_gap_outcome TYPE record<regime>;
DEFINE FIELD IF NOT EXISTS earnings_date     ON price_gap_outcome TYPE datetime;
DEFINE FIELD IF NOT EXISTS prev_close        ON price_gap_outcome TYPE float;
DEFINE FIELD IF NOT EXISTS next_open         ON price_gap_outcome TYPE float;
DEFINE FIELD IF NOT EXISTS gap_pct           ON price_gap_outcome TYPE float;
DEFINE FIELD IF NOT EXISTS gap_direction     ON price_gap_outcome TYPE string;
DEFINE FIELD IF NOT EXISTS benchmark_return  ON price_gap_outcome TYPE option<float>;
DEFINE FIELD IF NOT EXISTS relative_gap      ON price_gap_outcome TYPE option<float>;
DEFINE FIELD IF NOT EXISTS is_extended_hours ON price_gap_outcome TYPE bool;
DEFINE INDEX IF NOT EXISTS idx_pgo ON price_gap_outcome FIELDS company, transcript UNIQUE;

-- Backtest run metadata
DEFINE TABLE IF NOT EXISTS backtest_run SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS run_id              ON backtest_run TYPE string;
DEFINE FIELD IF NOT EXISTS run_at              ON backtest_run TYPE datetime;
DEFINE FIELD IF NOT EXISTS ticker_universe     ON backtest_run TYPE array<string>;
DEFINE FIELD IF NOT EXISTS from_date           ON backtest_run TYPE datetime;
DEFINE FIELD IF NOT EXISTS to_date             ON backtest_run TYPE datetime;
DEFINE FIELD IF NOT EXISTS granularity         ON backtest_run TYPE string;
DEFINE FIELD IF NOT EXISTS sentiment_threshold ON backtest_run TYPE float;
DEFINE FIELD IF NOT EXISTS benchmark           ON backtest_run TYPE string;
DEFINE FIELD IF NOT EXISTS include_ext_hours   ON backtest_run TYPE bool;
DEFINE FIELD IF NOT EXISTS event_type          ON backtest_run TYPE string;
DEFINE FIELD IF NOT EXISTS directional_accuracy ON backtest_run TYPE option<float>;
DEFINE FIELD IF NOT EXISTS hit_rate_by_regime  ON backtest_run TYPE option<object>;
DEFINE INDEX IF NOT EXISTS idx_br ON backtest_run FIELDS run_id UNIQUE;

-- Edge: transcript_doc → regime
DEFINE TABLE IF NOT EXISTS occurred_during SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS in  ON occurred_during TYPE record<transcript_doc>;
DEFINE FIELD IF NOT EXISTS out ON occurred_during TYPE record<regime>;

-- Edge: price_gap_outcome → backtest_run
DEFINE TABLE IF NOT EXISTS predicted_by SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS in                  ON predicted_by TYPE record<price_gap_outcome>;
DEFINE FIELD IF NOT EXISTS out                 ON predicted_by TYPE record<backtest_run>;
DEFINE FIELD IF NOT EXISTS signal_bundle       ON predicted_by TYPE option<object>;
DEFINE FIELD IF NOT EXISTS composite_score     ON predicted_by TYPE option<float>;
DEFINE FIELD IF NOT EXISTS signals_json        ON predicted_by TYPE option<string>;
DEFINE FIELD IF NOT EXISTS predicted_direction ON predicted_by TYPE string;
DEFINE FIELD IF NOT EXISTS correct             ON predicted_by TYPE bool;
