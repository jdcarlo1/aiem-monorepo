---
name: Layer 9 Statistical Edge
description: Two new isolated quant modules wired only into AI-generated signal paths; zero effect on scanner tabs.
---

## Files
- `artifacts/stock-scanner-api/advanced_quant_indicators.py` — pure math (Hurst, VPIN, Roll, Corwin-Schultz, Amihud, skew/kurtosis, jump detection, entropy, PCA, absorption ratio, VRP, skew velocity). No DB, no HTTP.
- `artifacts/stock-scanner-api/layer9_statistical_edge.py` — scoring engine (0-100). Entry points: `compute_layer9_score(ticker, history_df)`, `batch_layer9_scores(dict)`, `format_layer9_signal(result)`.

## Weights (sum to 1.0)
hurst_regime=0.20, vpin_toxicity=0.20, illiquidity_penalty=0.20 (inverted), tail_risk=0.15, entropy_clarity=0.15, jump_risk=0.10

## Data source
`_td_history(ticker, days=120)` — already Tradier-backed, returns Close/High/Low/Volume DataFrame. Minimum 30 rows required.

## Wiring in main.py — AI signal path ONLY
| Location | What it does |
|---|---|
| `_enrich_layer9_signals(tickers_data)` (new helper ~line 28009) | Batch-fetches histories, runs Layer 9, writes stat9_score/regime/vpin/jump/entropy/tail into each ticker dict |
| `_ai_trades_worker()` | Calls `_enrich_layer9_signals` after `_enrich_technical_signals`; appends `stat9_signal` to sig_text; second LLM call generates 3 stock_picks[] |
| `_bg_aisc()` (AI short calls) | Per-pick enrichment: adds stat9_score, stat9_regime, stat9_signal, stat9_jump |
| `_mkt_layer9_score(ticker, days)` | AIEM tool function; in both tool maps + `_AIEM_AGENT_TOOLS` schema |

## Output fields added to AI picks
- Call picks: `stat9=XX regime=... vpin=... jump=... entropy=... tail_risk=...` in prompt signal line
- AI trades cache: new `stock_picks: []` key alongside existing `trades: []`
- AI short calls picks: `stat9_score`, `stat9_regime`, `stat9_signal`, `stat9_jump` per pick

## What NOT touched
High Conviction, Unusual Calls, Bull Flow, EOD Sweeps, any scanner tab cache, any DB table.

**Why:** User explicitly required isolation — Layer 9 is an AI-signal enhancement only, not a scanner signal.
