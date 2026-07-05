---
name: Momentum Trade Model v3
description: State of the pre-move momentum trade detector after v3 rebuild (24 features, 4 gates)
---

# Momentum Trade Model v3

## Status
- PKL path: `artifacts/stock-scanner-api/aiem_momentum_trade.pkl`
- Trained at: 2026-07-05 21:39 UTC (interim model on 2025-06-01 to 2026-04-01 data)
- Full retrain (2024-07-01) triggered 2026-07-05 21:43 UTC, running in server background thread

## Features (24 total)
**Core 14 (original):** range_pct, range_trend, vol_vs_20d, vol_trend, vs_20d_high, vs_20d_low, mom_5d, mom_20d, mom_60d, low_stability, gap_pct, close_strength, price_vs_52wh, rvol

**New 10 (v3 additions):** rsi_14, cmf_20, obv_trend, atr_pct, stoch_k, bb_pct, vwap_dev, vs_ma50, vs_ma200, price_vs_52wl

All computed via 2-CTE SQL from polygon_market_daily (no live API needed). SQL uses high_price, low_price, vwap, prev_close.

## 4 Hard Filter Gates (statistically validated, p<0.0001, 900K rows)
1. vs_20d_high ≤ 0.88 (coiled below recent high)
2. vol_vs_20d ≤ 1.05 (volume quiet)
3. price ≤ $25 (precision gate: $3-10 range gives 13.5% WR vs $50+ at 4.7%)
4. month NOT in {11, 12, 1, 2} (seasonal blackout: winter WR 3-4% vs spring 11-17%)

## Performance (interim model on ~262K rows)
- AUC: 0.826
- Top signals: atr_pct, vs_20d_high, vs_ma50
- Combined gate effect: Price<$25 + excl Nov-Feb = 13.3% WR vs 8.6% baseline

## Key Bugs Fixed
- `run_momentum_trade_train`: conn.close() was in `finally` before `run_filter_sweep(conn)` → connection already closed error → fixed to close conn AFTER filter sweep via try/finally
- `run_filter_sweep` SQL: only had 14 features → KeyError on v3 model's 24 features → updated to full 2-CTE structure with all 24 features
- `momentum_trade_score` X construction: used module-level FEATURE_COLUMNS instead of art["feature_cols"] → dimension mismatch when pkl has different feature count → fixed to use art.get("feature_cols", FEATURE_COLUMNS)
- model_version was hardcoded "v3_24features" → made dynamic: f"v3_{len(_active_feat_cols)}features"

## Backward Compatibility
Scorer uses art.get("feature_cols", FEATURE_COLUMNS) so it works correctly with both v2 (14 features) and v3 (24 features) pkl files.

**Why:** The pkl might be v2 while v3 retrain is running. Dimension mismatch = crash.
**How to apply:** Any future feature additions must also update (1) FEATURE_COLUMNS, (2) _build_dataset SQL, (3) run_filter_sweep SQL, (4) momentum_trade_score live computation, (5) AIEM schema description.
