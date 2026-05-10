# Backtest Evaluation Report — Layer 4

Layer 4 measures the predictive quality of the backtesting pipeline. It queries all `predicted_by` edges for a completed `backtest_run` and computes 7 performance metrics across every earnings event that was processed.

---

## Running the Evaluation

### Option A — CLI eval runner (recommended)

```powershell
# Run a backtest first (writes .last_run_id automatically)
python scripts/datamanager.py backtest --ticker SYN001 --ticker SYN002 --from 2018-01-01 --to 2018-12-31

# Then evaluate that run
python -m eval.runner backtest --run-id (Get-Content .last_run_id)

# Or evaluate with regime breakdown
python -m eval.runner backtest --run-id (Get-Content .last_run_id) --stratify-by regime
```

### Option B — datamanager shortcut

```powershell
python scripts/datamanager.py backtest-report --run-id (Get-Content .last_run_id)
```

### Option C — full ticker universe

```powershell
# Run across all 50 synthetic tickers, 2018–2022
python scripts/datamanager.py backtest --universe all --from 2018-01-01 --to 2022-12-31

# Evaluate
python -m eval.runner backtest --run-id (Get-Content .last_run_id) --stratify-by regime
```

---

## What the Report Contains

The evaluator queries SurrealDB for every `predicted_by` edge whose `out` points to the given `backtest_run`, enriches each prediction with its linked `price_gap_outcome` (including regime label), and computes the following 7 metrics.

### 1. `directional_accuracy`

```
correct_predictions / total_predictions
```

The fraction of events where the model's predicted price move direction (`up` / `down` / `flat`) matched the actual post-earnings gap direction.

- **Prediction rule:** `composite_score > threshold → "up"`, `< -threshold → "down"`, else `"flat"`
- **Gap direction rule:** `gap_pct > 0.5% → "up"`, `< -0.5% → "down"`, else `"flat"`
- **Target:** ≥ 0.55 (55% — better than a coin flip)

### 2. `hit_rate_by_regime`

```
directional_accuracy split by regime.label
```

The same accuracy metric broken down per market regime. Shows whether the signal bundle performs differently across macro environments.

Expected keys (all 4 regimes must be present after a full run):

| Regime | Years | What drives it |
|:---|:---|:---|
| `GrowthExpansion` | 2016–2019 | Revenue acceleration, rate guidance |
| `BlackSwan` | 2020–2021 | Supply chain, tail risk |
| `HighInflation` | 2022–2023 | Pricing power, margin compression |
| `AIExpansion` | 2024–2026 | Capex efficiency, AI keywords |

A large gap between regimes (e.g., 0.71 for AIExpansion vs 0.48 for BlackSwan) signals that the model is regime-sensitive and may need regime-specific signal weights.

### 3. `signal_attribution`

```
For each named signal:
  correct_mean   = mean score across correct predictions
  incorrect_mean = mean score across incorrect predictions
```

Identifies which signals actually predict the gap direction. A signal with `correct_mean >> incorrect_mean` is carrying predictive weight; one where both means are similar is noise.

Example interpretation:
```json
"management_confidence_shift": {"correct_mean": 0.42, "incorrect_mean": 0.12}
```
This signal fires much higher on correct calls — it is load-bearing. If you see `correct_mean ≈ incorrect_mean`, the signal contributes nothing to accuracy.

### 4. `precision_bull` and `precision_bear`

```
precision_bull = TP_bull / (TP_bull + FP_bull)
precision_bear = TP_bear / (TP_bear + FP_bear)
```

Of all events predicted as "up" (bull), what fraction actually gapped up? Same for "down" (bear). This measures how reliable a directional call is when the model makes one.

- **Target:** ≥ 0.55 for both
- If `precision_bull` is high but `precision_bear` is low: the model is better at detecting positive surprises than negative ones (common with management-forward transcripts).

### 5. `threshold_sensitivity`

```
For each threshold t ∈ {0.1, 0.2, 0.3, 0.4}:
  re-classify every event using composite_score > t → "up" etc.
  accuracy = correct / total
```

Shows how directional accuracy changes as the `sentiment_threshold` tightens. A higher threshold means fewer events are called directional (more "flat") — but those that are called tend to be higher-conviction.

Use this to find the optimal threshold for a given universe:
- **Low threshold (0.1):** noisy, more calls, lower precision
- **High threshold (0.4):** fewer calls, potentially higher precision but more missed events

### 6. `relative_gap_accuracy`

```
For events where relative_gap is non-null:
  actual_relative_dir = "up" if relative_gap > 0.5%, "down" if < -0.5%, else "flat"
  correct = predicted_direction == actual_relative_dir
  relative_gap_accuracy = correct / total_with_relative_gap
```

Measures accuracy against `relative_gap = gap_pct − benchmark_return` (SPY-adjusted). This removes market-wide moves and isolates company-specific signal quality. A model that looks great on raw `directional_accuracy` but poor on `relative_gap_accuracy` is just riding market momentum, not predicting company-level outcomes.

---

## Example Output

```json
{
  "run_id": "a2ed8c99-aa13-4629-be73-7839e09184ed",
  "total_predictions": 8,
  "directional_accuracy": 0.625,
  "hit_rate_by_regime": {
    "GrowthExpansion": 0.6667,
    "BlackSwan": 0.5,
    "HighInflation": 0.6,
    "AIExpansion": 0.75
  },
  "signal_attribution": {
    "management_confidence_shift": {
      "correct_mean": 0.42,
      "incorrect_mean": 0.12
    },
    "guidance_gap": {
      "correct_mean": 0.38,
      "incorrect_mean": 0.08
    }
  },
  "precision_bull": 0.6,
  "precision_bear": 0.5,
  "threshold_sensitivity": {
    "0.1": 0.625,
    "0.2": 0.625,
    "0.3": 0.5,
    "0.4": 0.5
  },
  "relative_gap_accuracy": 0.5714
}
```

---

## Pass Criteria (Step 5 of Build Order)

From `docs/backtesting_pipeline/plan.final.md §8`:

| Check | Pass condition |
|:---|:---|
| All 7 metrics present | `directional_accuracy`, `hit_rate_by_regime`, `signal_attribution`, `precision_bull`, `precision_bear`, `threshold_sensitivity`, `relative_gap_accuracy` all non-null in output |
| Regime coverage | `hit_rate_by_regime` contains all 4 regime keys after a full-universe run |
| No null signal bundles | Every `predicted_by` edge has a non-null `signal_bundle` with `composite_score` |

Run the functional test suite to verify Step 4 pass criteria (the backtest itself):

```powershell
pytest tests/functional/test_backtest_agent.py -v
```

---

## How Data Flows Into This Report

```
transcript_doc (JSON on disk)
        ↓  backtest_agent.py — process_all_events
price_gap_outcome  ←→  occurred_during  ←→  regime
        ↓
predicted_by edge  (signal_bundle, predicted_direction, correct)
        ↓  out →
backtest_run  (directional_accuracy, hit_rate_by_regime stored after run)
        ↓
eval/evaluators/backtest_eval.py — evaluate_backtest()
        ↓
python -m eval.runner backtest --run-id UUID
```

The evaluator does **not** recompute signals — it reads `correct` and `signal_bundle` directly from the `predicted_by` edges written during the backtest run.
