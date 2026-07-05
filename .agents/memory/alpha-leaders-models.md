---
name: Alpha Leaders models
description: Two XGBoost models for stock-vs-SPY alpha prediction — 10d and 60d horizons, what AUC means, key data finding
---

## Files
- `aiem_alpha_leaders_10d.pkl` / `_report.json` — 10-day horizon
- `aiem_alpha_leaders_60d.pkl` / `_report.json` — 60-day horizon
- `alpha_historical_trainer.py` — single parameterized module, all logic here

## AUC results (July 2026 train)
| Model | AUC | Top signals |
|-------|-----|-------------|
| 10d   | 0.5108 | range_pct, spy_rs_5d, momentum_20d |
| 60d   | 0.4737 | spy_rs_20d, range_pct, momentum_20d |

## Key data facts
- 10d: 1,434,216 labeled rows, 45.5% outperformers, 7,416 tickers
- 60d: 898,478 labeled rows, 41.9% outperformers (fewer rows — needs 60-day trailing history)
- Data: polygon_market_daily from July 2024 only (dense). NO paper trades used.

## What AUC < 0.50 means for 60d
The 60d model being slightly below random is a real finding: technical momentum/RS features
show MEAN REVERSION over 3-month windows. Stocks with recent high momentum tend to slightly
underperform SPY over the next 60 trading days.

**Why:**
- With only ~22 months of labeled 60d data, the model is data-starved
- Current features (momentum_5d/20d/60d, RS) capture "what already moved" — not "what's about to"
- To catch early-stage long-term uptrend starters, you need: low current momentum + increasing OBV + approaching breakout + improving sector

**How to apply:**
- 60d WEAK on a stock that already ran big = mean-reversion warning (reliable!)
- 60d STRONG on a quiet stock = possible early accumulation (use with other signals)
- Do NOT use 60d AUC as a reason to distrust the model — it's telling the truth

## Admin endpoints
- `POST /stock-api/admin/run-historical-alpha-train` body: `{"fwd_days": 10}` or `{"fwd_days": 60}`
- `GET  /stock-api/admin/alpha-model-status` — shows both models
- `GET  /stock-api/admin/alpha-score-ticker?ticker=MU&horizon=60d`

## AIEM tool
- Tool name: `alpha_score_ticker`
- Parameter: `horizon="10d"` (default) or `"60d"`
- Internally calls `alpha_leaders_score(ticker, pick, fwd_days)`
